const API_BASE = '/api';
const ADMIN_BASE = '/admin';

// Check auth on load
async function checkAuth() {
    try {
        const response = await fetch(`${ADMIN_BASE}/personas`, {
            headers: { 'Authorization': `Basic ${btoa('admin:admin')}` }
        });
        if (response.status === 401) {
            alert('Unauthorized. Please log in with admin/admin');
            window.location.href = '/';
        }
    } catch (e) {
        console.error('Auth check failed:', e);
    }
}

function getAuthHeaders() {
    return { 'Authorization': `Basic ${btoa('admin:admin')}` };
}

async function loadPersonas() {
    try {
        const personas = await fetch(`${ADMIN_BASE}/personas`, {
            headers: getAuthHeaders()
        }).then(r => r.json());
        
        displayPersonas(personas);
        document.getElementById('personaCount').textContent = personas.length;
    } catch (error) {
        console.error('Failed to load personas:', error);
    }
}

function displayPersonas(personas) {
    const list = document.getElementById('personasList');
    list.innerHTML = personas.map((p, index) => `
        <div class="persona-item" id="persona-${index}">
            <div class="persona-info">
                <strong>${escapeHtml(p.name)}</strong>
                <p>${escapeHtml(p.style)}</p>
            </div>
            <div class="persona-actions">
                <button class="edit-btn" onclick="editPersona(${index})">Edit</button>
                <button class="delete-btn" onclick="deletePersona(${index})">Delete</button>
            </div>
        </div>
    `).join('');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function addPersona() {
    const name = document.getElementById('newPersonaName').value.trim();
    const style = document.getElementById('newPersonaStyle').value.trim();
    
    if (!name || !style) return alert('Please fill all fields');
    
    try {
        await fetch(`${ADMIN_BASE}/personas`, {
            method: 'POST',
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ name, style })
        });
        
        document.getElementById('newPersonaName').value = '';
        document.getElementById('newPersonaStyle').value = '';
        await loadPersonas();
    } catch (error) {
        alert('Failed to add persona');
    }
}

async function deletePersona(index) {
    if (!confirm('Delete this persona? AI will stop using it.')) return;
    
    try {
        await fetch(`${ADMIN_BASE}/personas/${index}`, {
            method: 'DELETE',
            headers: getAuthHeaders()
        });
        await loadPersonas();
    } catch (error) {
        alert('Failed to delete persona');
    }
}

async function editPersona(index) {
    const item = document.getElementById(`persona-${index}`);
    const nameEl = item.querySelector('strong');
    const styleEl = item.querySelector('p');
    
    const newName = prompt('New name:', nameEl.textContent);
    const newStyle = prompt('New style:', styleEl.textContent);
    
    if (!newName || !newStyle) return;
    
    try {
        await fetch(`${ADMIN_BASE}/personas/${index}`, {
            method: 'PUT',
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ name: newName, style: newStyle })
        });
        await loadPersonas();
    } catch (error) {
        alert('Failed to update persona');
    }
}

function logout() {
    // Basic auth can't really be "logged out", but we can redirect
    window.location.href = '/';
}

function loadSystemStats() {
    // These would come from the server in a real app
    document.getElementById('modelCount').textContent = '2';
    document.getElementById('gpuCount').textContent = '2';
}

// Initialize
window.onload = () => {
    checkAuth();
    loadPersonas();
    loadSystemStats();
};
