const API_BASE = '/api';
const FEED = document.getElementById('postsFeed');

async function apiCall(url, options = {}) {
    try {
        const response = await fetch(url, options);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
    } catch (error) {
        console.error('API Error:', error);
        throw error;
    }
}

async function loadPosts() {
    FEED.innerHTML = '<div class="loading">Loading posts...</div>';
    try {
        const posts = await apiCall(`${API_BASE}/posts`);
        displayPosts(posts);
    } catch (error) {
        FEED.innerHTML = '<div class="loading">❌ Failed to load posts. Check server.</div>';
    }
}

function displayPosts(posts) {
    if (!posts.length) {
        FEED.innerHTML = '<div class="loading">No posts yet. Create or search for one!</div>';
        return;
    }

    FEED.innerHTML = posts.map(post => `
        <div class="post" id="post-${post.id}">
            <div class="post-header">
                <span class="post-author">
                    ${escapeHtml(post.author)}
                    ${post.is_ai_generated ? '<span class="ai-badge">🤖 AI</span>' : ''}
                </span>
                <span class="post-time">${new Date(post.created_at).toLocaleString()}</span>
            </div>
            <div class="post-content">${escapeHtml(post.content)}</div>
            <div class="post-actions">
                <span class="comment-count">💬 ${post.comment_count || 0} comments</span>
                <button class="refresh-btn" onclick="loadComments(${post.id})">🔄 Refresh</button>
            </div>
            <div class="comments-section" id="comments-${post.id}">
                <div class="loading">Loading comments...</div>
            </div>
            <div class="add-comment">
                <input type="text" placeholder="Write a comment..." 
                       id="comment-input-${post.id}" 
                       onkeypress="handleEnter(event, ${post.id})">
                <button class="reply-btn" onclick="addComment(${post.id})">Reply</button>
            </div>
        </div>
    `).join('');
    
    posts.forEach(p => setTimeout(() => loadComments(p.id), 300));
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function loadComments(postId) {
    const container = document.getElementById(`comments-${postId}`);
    try {
        const comments = await apiCall(`${API_BASE}/posts/${postId}/comments`);
        container.innerHTML = comments.length ? 
            comments.map(c => `
                <div class="comment">
                    <div class="comment-author">${escapeHtml(c.author)}</div>
                    <div class="comment-content">${escapeHtml(c.content)}</div>
                </div>
            `).join('') : 
            '<div style="color: #65676b; font-size: 14px; padding: 10px;">No comments yet. Be the first!</div>';
    } catch (error) {
        container.innerHTML = '<div style="color: red;">❌ Failed to load comments</div>';
    }
}

async function createPost() {
    const content = document.getElementById('postContent').value.trim();
    const author = document.getElementById('postAuthor').value.trim() || 'Anonymous';
    
    if (!content) return alert('Please write something!');
    
    const btn = document.querySelector('.post-creator button');
    btn.disabled = true;
    btn.textContent = 'Posting...';
    
    try {
        await apiCall(`${API_BASE}/posts`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({content, author})
        });
        
        document.getElementById('postContent').value = '';
        await loadPosts();
    } catch (error) {
        alert('❌ Failed to create post');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Post';
    }
}

async function addComment(postId) {
    const input = document.getElementById(`comment-input-${postId}`);
    const content = input.value.trim();
    
    if (!content) return;
    
    input.disabled = true;
    const btn = input.nextElementSibling;
    btn.textContent = '...';
    
    try {
        await apiCall(`${API_BASE}/posts/${postId}/comments`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({content, author: 'You'})
        });
        
        input.value = '';
        await loadComments(postId);
    } catch (error) {
        alert('❌ Failed to add comment');
    } finally {
        input.disabled = false;
        btn.textContent = 'Reply';
        input.focus();
    }
}

function handleEnter(event, postId) {
    if (event.key === 'Enter') addComment(postId);
}

async function searchAndGenerate() {
    const input = document.getElementById('searchInput');
    const btn = document.getElementById('searchBtn');
    const query = input.value.trim();
    
    if (!query) return alert('Enter a search term!');
    
    btn.disabled = true;
    btn.textContent = '🤖 Generating...';
    
    try {
        await apiCall(`${API_BASE}/search?q=${encodeURIComponent(query)}`);
        input.value = '';
        await loadPosts();
    } catch (error) {
        alert('❌ Generation failed. Check server logs.');
    } finally {
        btn.disabled = false;
        btn.textContent = '🤖 Generate Posts';
    }
}

// Initialize and auto-refresh
window.onload = loadPosts;
setInterval(loadPosts, 20000); // Refresh every 20s

