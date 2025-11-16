#!/usr/bin/env python3
import os
import sqlite3
import threading
import queue
import time
import re
import json
from concurrent.futures import ThreadPoolExecutor, wait
from flask import Flask, request, jsonify, send_from_directory
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
import torch

# -------------------------------
# CONFIGURATION
# -------------------------------
MODEL_ID = "Qwen/Qwen3-4B-Instruct-2507"
DB_PATH = "ofsocial.db"
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
MAX_POST_LEN = 750
MAX_COMMENT_LEN = 750
NUM_AI_POSTS = 2

# Parallel processing config
MAX_WORKERS_PER_GPU = 1  # Adjust based on GPU memory (2-4 recommended)

# Admin credentials (hardcoded)
ADMIN_USER = "admin"
ADMIN_PASS_HASH = generate_password_hash("admin")

# Instruction artifacts to filter out
ARTIFACT_PATTERNS = [
    r"No hashtags\.*",
    r"Include.*?:",
    r"Use (at least \d+ )?emojis?",
    r"Use internet slang",
    r"Use sarcasm",
    r"Story:",
    r"The story must be",
    r"No passive voice",
    r"Keep it under",
    r"Write a short comment",
    r"as .{0,30}\.",
    r"Be .{0,30}\.",
]

# -------------------------------
# PERSISTENT PERSONA STORAGE
# -------------------------------
class PersonaManager:
    def __init__(self, db_path="personas.json"):
        self.db_path = db_path
        self.default_personas = [
            {"name": "Alex Tech", "style": "Technical expert, precise, uses jargon naturally"},
            {"name": "Sam Casual", "style": "Friendly, uses emojis naturally, conversational"},
            {"name": "Dr. Morgan", "style": "Academic tone, analytical, cites studies naturally"},
            {"name": "Charlie", "style": "Sarcastic internet troll, uses slang, contrarian"},
        ]
        self.load_personas()
    
    def load_personas(self):
        if os.path.exists(self.db_path):
            with open(self.db_path, 'r') as f:
                self.personas = json.load(f)
        else:
            self.personas = self.default_personas.copy()
            self.save_personas()
    
    def save_personas(self):
        with open(self.db_path, 'w') as f:
            json.dump(self.personas, f, indent=2)
    
    def get_all(self):
        return self.personas
    
    def add(self, name, style):
        self.personas.append({"name": name, "style": style})
        self.save_personas()
    
    def update(self, index, name, style):
        if 0 <= index < len(self.personas):
            self.personas[index] = {"name": name, "style": style}
            self.save_personas()
    
    def delete(self, index):
        if 0 <= index < len(self.personas):
            del self.personas[index]
            self.save_personas()

# -------------------------------
# MODEL MANAGER
# -------------------------------
class DualModelManager:
    def __init__(self):
        print("🤖 Loading tokenizer...")
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
        
        bnb_config = BitsAndBytesConfig(load_in_8bit=True)
        gpu_count = torch.cuda.device_count()
        
        if gpu_count >= 2:
            self.devices = [torch.device("cuda:0"), torch.device("cuda:1")]
            print(f"✅ Found {gpu_count} GPUs. Using cuda:0 and cuda:1")
        elif gpu_count == 1:
            self.devices = [torch.device("cuda:0"), torch.device("cuda:0")]
            print("⚠️ Only 1 GPU detected. Models will share cuda:0")
        else:
            raise RuntimeError("❌ No CUDA GPUs found!")
        
        self.models = []
        self.locks = []
        for i, dev in enumerate(self.devices):
            print(f"📦 Loading Model {i+1} on {dev}...")
            start = time.time()
            model = AutoModelForCausalLM.from_pretrained(
                MODEL_ID,
                quantization_config=bnb_config,
                device_map={"": dev.index},
                trust_remote_code=True,
                torch_dtype=torch.float16,
            ).eval()
            self.models.append(model)
            self.locks.append(threading.Lock())
            print(f"✅ Model {i+1} loaded in {time.time()-start:.1f}s")
    
    def generate(self, prompt, max_tokens=300, temperature=0.8, model_id=0):
        """Thread-safe generation with better parameters"""
        model = self.models[model_id]
        lock = self.locks[model_id]
        device = self.devices[model_id]
        
        with lock:
            inputs = self.tokenizer(prompt, return_tensors="pt", truncation=True, max_length=2048)
            inputs = {k: v.to(device) for k, v in inputs.items()}
            
            with torch.no_grad():
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=max_tokens,
                    temperature=temperature,
                    do_sample=True,
                    top_p=0.95,
                    repetition_penalty=1.15,
                    pad_token_id=self.tokenizer.eos_token_id,
                )
            
            generated = outputs[0][inputs["input_ids"].shape[-1]:]
            return self.tokenizer.decode(generated, skip_special_tokens=True)

# -------------------------------
# CONTENT CLEANING & GENERATION
# -------------------------------
def clean_content(text):
    """Aggressively remove prompt artifacts and instructions"""
    original = text
    
    # Remove patterns
    for pattern in ARTIFACT_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    
    # Remove bracketed instructions
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"\([^\)]*instructions[^\)]*\)", "", text, flags=re.IGNORECASE)
    
    # Remove multiple spaces and newlines
    text = re.sub(r'\s+', ' ', text).strip()
    
    # Take first complete sentence or meaningful chunk
    sentences = re.split(r'[.!?]+', text)
    if sentences and len(sentences[0]) > 20:
        text = sentences[0] + '.'
    
    # If still contains obvious instructions, return first line only
    if any(word in text.lower() for word in ['include', 'use at least', 'write a', 'post:']):
        lines = [l.strip() for l in original.split('\n') if l.strip() and len(l) > 20]
        if lines:
            text = lines[0]
    
    return text.strip()

def generate_post_content(topic, model_id=0):
    """Generate clean social media post"""
    prompt = f"""<|im_start|>system
You are a social media user. Write a natural post about "{topic}".
Rules:
- Write in your own voice, with personal opinion
- Keep it conversational
- Do NOT include instructions or meta-commentary
- Do NOT mention "No hashtags" or any rules
- Just write the post content
<|im_end|>
<|im_start|>user
Write a post about: {topic}
<|im_end|>
<|im_start|>assistant
"""
    
    for attempt in range(2):
        raw = model_manager.generate(prompt, max_tokens=400, temperature=0.85, model_id=model_id)
        cleaned = clean_content(raw)
        
        if cleaned and len(cleaned) > 30 and not any(artifact in cleaned.lower() for artifact in ['no hashtags', 'include', 'use at least', 'write a comment', 'post:']):
            return cleaned[:MAX_POST_LEN]
        
        print(f"⚠️ Post artifact detected, retrying... (attempt {attempt + 1})")
    
    return cleaned[:MAX_POST_LEN] if cleaned else f"I've been thinking about {topic} lately..."

def generate_comment_content(post_content, persona, model_id=0):
    """Generate clean comment with persona"""
    prompt = f"""<|im_start|>system
You are {persona['name']}. Your communication style: {persona['style']}
Rules:
- Respond naturally to the post
- Do NOT include instructions or meta-commentary
- Do NOT mention "No hashtags" or any rules
- Just write the comment content
<|im_end|>
<|im_start|>user
Post: "{post_content}"
Write a natural comment as {persona['name']}.
<|im_end|>
<|im_start|>assistant
"""
    
    for attempt in range(2):
        raw = model_manager.generate(prompt, max_tokens=200, temperature=0.9, model_id=model_id)
        cleaned = clean_content(raw)
        
        if cleaned and len(cleaned) > 15 and not any(artifact in cleaned.lower() for artifact in ['include', 'use', 'write a comment']):
            return cleaned[:MAX_COMMENT_LEN]
        
        print(f"⚠️ Comment artifact detected, retrying... (attempt {attempt + 1})")
    
    return cleaned[:MAX_COMMENT_LEN] if cleaned else "Interesting post!"

# -------------------------------
# PARALLEL EXECUTION
# -------------------------------
class ParallelCommentGenerator:
    def __init__(self, model_manager, persona_manager, db):
        self.model_manager = model_manager
        self.persona_manager = persona_manager
        self.db = db
        self.executor = None
        self._lock = threading.Lock()
        self._initialize_executor()
    
    def _initialize_executor(self):
        """Initialize thread pool executor with optimal worker count"""
        num_gpus = len(self.model_manager.models)
        max_workers = num_gpus * MAX_WORKERS_PER_GPU
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="comment_worker"
        )
        print(f"🚀 Initialized parallel executor: {max_workers} workers ({MAX_WORKERS_PER_GPU} per GPU)")
    
    def submit_post_comments(self, post_id, post_content):
        """Submit all comment generation tasks for a post in parallel"""
        active_personas = self.persona_manager.get_all()
        futures = []
        
        for i, persona in enumerate(active_personas):
            device_id = i % len(self.model_manager.models)
            
            # Submit each comment as separate task
            future = self.executor.submit(
                self._generate_single_comment,
                post_id=post_id,
                post_content=post_content,
                persona=persona,
                device_id=device_id
            )
            futures.append(future)
        
        return futures
    
    def _generate_single_comment(self, post_id, post_content, persona, device_id):
        """Generate a single comment and save to DB"""
        try:
            comment = generate_comment_content(post_content, persona, device_id)
            self.db.add_comment(post_id, comment, persona['name'])
            print(f"🤖 {persona['name']} → post #{post_id} [GPU{device_id}] ✅")
        except Exception as e:
            print(f"❌ Comment error [GPU{device_id}]: {e}")
    
    def shutdown(self, wait=True):
        """Shutdown the executor gracefully"""
        if self.executor:
            self.executor.shutdown(wait=wait)
            print("🛑 Parallel executor shutdown complete")

# -------------------------------
# DATABASE
# -------------------------------
class Database:
    def __init__(self):
        db_dir = os.path.dirname(DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self.init_db()
        self.lock = threading.Lock()
    
    def init_db(self):
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    author TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_ai_generated BOOLEAN DEFAULT 0
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    author TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (post_id) REFERENCES posts (id)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id)")
            conn.commit()
    
    def add_post(self, content, author, is_ai=False):
        with self.lock:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO posts (content, author, is_ai_generated) VALUES (?, ?, ?)",
                    (content[:MAX_POST_LEN], author, is_ai)
                )
                conn.commit()
                return cursor.lastrowid
    
    def add_comment(self, post_id, content, author):
        with self.lock:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO comments (post_id, content, author) VALUES (?, ?, ?)",
                    (post_id, content[:MAX_COMMENT_LEN], author)
                )
                conn.commit()
    
    def get_posts(self):
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.*, COUNT(c.id) as comment_count 
                FROM posts p 
                LEFT JOIN comments c ON p.id = c.post_id 
                GROUP BY p.id 
                ORDER BY p.created_at DESC
            """)
            return [{
                "id": r[0], "content": r[1], "author": r[2], 
                "created_at": r[3], "is_ai_generated": bool(r[4]), "comment_count": r[5]
            } for r in cursor.fetchall()]
    
    def get_comments(self, post_id):
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM comments WHERE post_id = ? ORDER BY created_at ASC",
                (post_id,)
            )
            return [{
                "id": r[0], "post_id": r[1], "content": r[2], "author": r[3], "created_at": r[4]
            } for r in cursor.fetchall()]

# -------------------------------
# FLASK APP
# -------------------------------
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='/static')
auth = HTTPBasicAuth()

# Global instances
model_manager = DualModelManager()
db = Database()
persona_manager = PersonaManager()
parallel_generator = None

# Admin auth verification
@auth.verify_password
def verify_password(username, password):
    if username == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, password):
        return username
    return None

# API Routes
@app.route('/api/posts')
def get_posts():
    return jsonify(db.get_posts())

@app.route('/api/posts', methods=['POST'])
def create_post():
    data = request.json
    content, author = data.get('content', '').strip(), data.get('author', 'Anonymous').strip()
    
    if not content:
        return jsonify({"error": "Empty content"}), 400
    
    post_id = db.add_post(content, author, False)
    
    # Submit all comment generation tasks in parallel
    parallel_generator.submit_post_comments(post_id, content)
    
    return jsonify({"success": True, "post_id": post_id})

@app.route('/api/posts/<int:post_id>/comments')
def get_comments(post_id):
    return jsonify(db.get_comments(post_id))

@app.route('/api/posts/<int:post_id>/comments', methods=['POST'])
def add_comment(post_id):
    data = request.json
    content, author = data.get('content', '').strip(), data.get('author', 'You').strip()
    
    if not content:
        return jsonify({"error": "Empty comment"}), 400
    
    db.add_comment(post_id, content, author)
    return jsonify({"success": True})

@app.route('/api/search')
def search_generate():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"error": "Empty query"}), 400
    
    try:
        ai_posts = []
        for i in range(NUM_AI_POSTS):
            content = generate_post_content(query, i % len(model_manager.models))
            post_id = db.add_post(content, f"AI Bot {i+1}", True)
            ai_posts.append({"id": post_id, "content": content, "author": f"AI Bot {i+1}"})
            
            # Generate comments in parallel for each AI post
            parallel_generator.submit_post_comments(post_id, content)
        
        return jsonify({"success": True, "posts": ai_posts})
    except Exception as e:
        print(f"Generation error: {e}")
        return jsonify({"error": str(e)}), 500

# Admin Routes
@app.route('/admin')
@auth.login_required
def admin_panel():
    return send_from_directory(STATIC_DIR, 'admin.html')

@app.route('/admin/personas', methods=['GET'])
@auth.login_required
def get_personas():
    return jsonify(persona_manager.get_all())

@app.route('/admin/personas', methods=['POST'])
@auth.login_required
def add_persona():
    data = request.json
    name = data.get('name', '').strip()
    style = data.get('style', '').strip()
    
    if not name or not style:
        return jsonify({"error": "Name and style required"}), 400
    
    persona_manager.add(name, style)
    return jsonify({"success": True})

@app.route('/admin/personas/<int:index>', methods=['PUT'])
@auth.login_required
def update_persona(index):
    data = request.json
    name = data.get('name', '').strip()
    style = data.get('style', '').strip()
    
    if not name or not style:
        return jsonify({"error": "Name and style required"}), 400
    
    persona_manager.update(index, name, style)
    return jsonify({"success": True})

@app.route('/admin/personas/<int:index>', methods=['DELETE'])
@auth.login_required
def delete_persona(index):
    if index >= len(persona_manager.get_all()):
        return jsonify({"error": "Invalid index"}), 404
    
    persona_manager.delete(index)
    return jsonify({"success": True})

@app.route('/')
def serve_index():
    return send_from_directory(STATIC_DIR, 'index.html')

# Cleanup on exit
import atexit
def cleanup():
    print("\n🛑 Shutting down OfSM Server...")
    if parallel_generator:
        parallel_generator.shutdown(wait=True)
    stop_event.set()
    worker_thread.join(timeout=5)
atexit.register(cleanup)

# Initialize parallel generator after model loading
parallel_generator = ParallelCommentGenerator(model_manager, persona_manager, db)

# -------------------------------
# MAIN
# -------------------------------
if __name__ == '__main__':
    print("\n" + "="*70)
    print("🚀 OfSM Server Running!")
    print(f"🌐 Main App: http://localhost:5000")
    print(f"🔐 Admin Panel: http://localhost:5000/admin (user: admin, pass: admin)")
    print(f"💾 Database: {os.path.abspath(DB_PATH)}")
    print(f"🤖 Model: {MODEL_ID}")
    print(f"💻 GPUs: {len(model_manager.models)} device(s)")
    print(f"⚡ Workers per GPU: {MAX_WORKERS_PER_GPU}")
    print(f"🎭 AI Personas: {len(persona_manager.get_all())}")
    print("="*70 + "\n")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
