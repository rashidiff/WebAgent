// content.js - Injected Webpage Content Script

// Function to traverse DOM and extract interactive elements in a clean JSON format
const MAX_INTERACTIVE_ELEMENTS = 200;

function getInteractiveDOM() {
  const interactiveTags = ['BUTTON', 'A', 'INPUT', 'SELECT', 'TEXTAREA'];
  const interactiveRoles = ['button', 'link', 'checkbox', 'tab', 'menuitem', 'option', 'combobox'];

  const allElements = document.querySelectorAll('*');
  const interactiveList = [];
  let agentIdCounter = 1;

  for (const el of allElements) {
    if (interactiveList.length >= MAX_INTERACTIVE_ELEMENTS) break;
    const tagName = el.tagName;
    const role = el.getAttribute('role');
    const hasOnClick = el.hasAttribute('onclick') || typeof el.onclick === 'function';
    const isContentEditable = el.getAttribute('contenteditable') === 'true';
    
    let isInteractive = interactiveTags.includes(tagName) ||
                        interactiveRoles.includes(role) ||
                        hasOnClick ||
                        isContentEditable;
                        
    // Exclude hidden inputs
    if (tagName === 'INPUT' && el.type === 'hidden') {
      isInteractive = false;
    }
    
    if (isInteractive) {
      // Check if visible
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      const isVisible = rect.width > 0 && 
                        rect.height > 0 && 
                        style.display !== 'none' && 
                        style.visibility !== 'hidden' &&
                        style.opacity !== '0';
                        
      if (isVisible) {
        // Tag with a temporary data-agent-id for 100% resilient targeting
        el.setAttribute('data-agent-id', agentIdCounter);
        
        let text = (el.innerText || el.textContent || "").trim();
        text = text.replace(/\s+/g, ' ').substring(0, 100);
        
        interactiveList.push({
          id: agentIdCounter,
          tagName: tagName,
          type: el.type || null,
          text: text,
          placeholder: el.getAttribute('placeholder') || null,
          value: el.value || null,
          selector: `[data-agent-id="${agentIdCounter}"]`,
          disabled: el.disabled || false,
          checked: el.checked || false,
          href: el.getAttribute('href') || null
        });
        
        agentIdCounter++;
      }
    }
  }


  return interactiveList;
}

// Listen for action executions and DOM extraction requests from the extension sidepanel
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  console.log("Content script received message:", request);
  
  if (request.type === 'get_dom') {
    try {
      const dom = getInteractiveDOM();
      sendResponse({ status: 'success', dom_tree: dom });
    } catch (e) {
      sendResponse({ status: 'error', error: e.message });
    }
  } else if (request.type === 'execute_action') {
    const { action, selector, value } = request;
    try {
      if (action === 'scroll') {
        if (value === 'down') {
          window.scrollBy({ top: window.innerHeight * 0.7, behavior: 'smooth' });
        } else if (value === 'up') {
          window.scrollBy({ top: -window.innerHeight * 0.7, behavior: 'smooth' });
        } else if (value === 'top') {
          window.scrollTo({ top: 0, behavior: 'smooth' });
        } else if (value === 'bottom') {
          window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
        }
      } else {
        const element = document.querySelector(selector);
        if (!element) {
          throw new Error(`Element not found with selector: ${selector}`);
        }
        
        if (action === 'click') {
          element.focus();
          
          // Dispatch mouse events to replicate natural user interactions
          element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
          element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
          element.click();
          
          if (element.tagName === 'INPUT' && (element.type === 'checkbox' || element.type === 'radio')) {
            element.dispatchEvent(new Event('change', { bubbles: true }));
          }
        } else if (action === 'input') {
          element.focus();
          element.value = value;
          
          // Dispatch input/change events for UI libraries (React, Vue, etc.) to capture state updates
          element.dispatchEvent(new Event('input', { bubbles: true }));
          element.dispatchEvent(new Event('change', { bubbles: true }));
        }
      }
      
      // Allow the page to run transition animations or make API updates, then send the new DOM state back
      setTimeout(() => {
        try {
          const dom = getInteractiveDOM();
          sendResponse({ status: 'success', dom_tree: dom });
        } catch (domErr) {
          sendResponse({ status: 'error', error: domErr.message });
        }
      }, 600);
      
    } catch (e) {
      sendResponse({ status: 'error', error: e.message });
    }
  }
  return true; // Retain message channel open for asynchronous sendResponse
});
