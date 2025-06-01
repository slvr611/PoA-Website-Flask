/**
 * Toggles the visibility of a section's content
 * @param {HTMLElement} header - The header element that was clicked
 */
function toggleSection(header) {
    const section = header.parentElement;
    const content = section.querySelector('.section-content');
    const icon = header.querySelector('.toggle-icon');
    
    if (content.style.display === 'none' || content.style.display === '') {
        content.style.display = 'block';
        icon.textContent = '▼';
        section.classList.add('section-expanded');
        
        // Save this section's expanded state to localStorage
        if (section.id) {
            localStorage.setItem('section-' + section.id, 'expanded');
        }
        
        // Dispatch a custom event that can be listened for
        section.dispatchEvent(new CustomEvent('section:expanded', { 
            bubbles: true,
            detail: { sectionId: section.id }
        }));
    } else {
        content.style.display = 'none';
        icon.textContent = '►';
        section.classList.remove('section-expanded');
        
        // Save this section's collapsed state to localStorage
        if (section.id) {
            localStorage.setItem('section-' + section.id, 'collapsed');
        }
        
        // Dispatch a custom event that can be listened for
        section.dispatchEvent(new CustomEvent('section:collapsed', { 
            bubbles: true,
            detail: { sectionId: section.id }
        }));
    }
}

/**
 * Initialize expandable sections
 * - Adds IDs to sections if they don't have them
 * - Restores expanded/collapsed state from localStorage
 * - Expands the first section by default if no saved state
 */
function initExpandableSections() {
    const sections = document.querySelectorAll('.expandable-section');
    let hasExpandedSection = false;
    
    sections.forEach((section, index) => {
        // Add ID if not present
        if (!section.id) {
            section.id = 'section-' + index;
        }
        
        const content = section.querySelector('.section-content');
        const icon = section.querySelector('.toggle-icon');
        const savedState = localStorage.getItem('section-' + section.id);
        
        // Restore saved state
        if (savedState === 'expanded') {
            content.style.display = 'block';
            icon.textContent = '▼';
            section.classList.add('section-expanded');
            hasExpandedSection = true;
            
            // Dispatch a custom event that can be listened for
            section.dispatchEvent(new CustomEvent('section:expanded', { 
                bubbles: true,
                detail: { sectionId: section.id }
            }));
        } else if (savedState === 'collapsed') {
            content.style.display = 'none';
            icon.textContent = '►';
            section.classList.remove('section-expanded');
        }
    });
    
    // If tech section is expanded on page load, trigger recentering
    const techSection = document.getElementById('tech-section');
    if (techSection && techSection.classList.contains('section-expanded')) {
        // Use setTimeout to ensure the tech tree is fully initialized
        setTimeout(() => {
            if (typeof window.recenterTechTree === 'function') {
                window.recenterTechTree();
            }
        }, 300); // Longer delay to ensure everything is loaded
    }
}

// Initialize when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', initExpandableSections);

