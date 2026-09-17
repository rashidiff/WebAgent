// content.js - Injected Webpage Content Script
if (!globalThis.__webAgentContentScriptLoaded) {
globalThis.__webAgentContentScriptLoaded = true;

const DOM_ELEMENT_BUDGET = 150;
const PAGE_SETTLE_DELAY_MS = 700;

function cleanText(text, maxLength = 160) {
  return (text || "").trim().replace(/\s+/g, " ").substring(0, maxLength);
}

function cssEscape(value) {
  if (window.CSS && typeof window.CSS.escape === "function") {
    return window.CSS.escape(value);
  }
  return String(value).replace(/["\\]/g, "\\$&");
}

function getLabelText(el) {
  if (el.labels && el.labels.length) {
    return cleanText(Array.from(el.labels).map((label) => label.innerText || label.textContent).join(" "));
  }

  const id = el.getAttribute("id");
  if (id) {
    const label = el.ownerDocument.querySelector(`label[for="${cssEscape(id)}"]`);
    if (label) return cleanText(label.innerText || label.textContent);
  }

  const parentLabel = el.closest("label");
  if (parentLabel) return cleanText(parentLabel.innerText || parentLabel.textContent);

  return null;
}

function isVisibleElement(el) {
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  return rect.width > 0 &&
    rect.height > 0 &&
    style.display !== "none" &&
    style.visibility !== "hidden" &&
    style.opacity !== "0";
}

function isInViewport(rect) {
  return rect.bottom >= 0 &&
    rect.right >= 0 &&
    rect.top <= window.innerHeight &&
    rect.left <= window.innerWidth;
}

function getElementFingerprint(el) {
  const signature = [
    el.tagName,
    el.getAttribute("role") || "",
    el.getAttribute("aria-label") || "",
    el.getAttribute("name") || "",
    el.getAttribute("id") || "",
    cleanText(el.innerText || el.textContent, 80),
    el.getAttribute("href") || ""
  ].join("|");

  let hash = 0;
  for (let i = 0; i < signature.length; i++) {
    hash = ((hash << 5) - hash) + signature.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash).toString(36);
}

function getSameOriginFrameDocument(frame) {
  try {
    return frame.contentDocument || (frame.contentWindow && frame.contentWindow.document) || null;
  } catch (err) {
    return null;
  }
}

function getReadableText(root = document, seenRoots = new Set()) {
  if (!root || seenRoots.has(root)) return "";
  seenRoots.add(root);

  const rootText = root.body
    ? root.body.innerText
    : (root.host ? root.textContent : root.documentElement && root.documentElement.innerText) || "";
  const pieces = [rootText];

  let elements = [];
  try {
    elements = Array.from(root.querySelectorAll("*"));
  } catch (err) {
    return pieces.filter(Boolean).join("\n");
  }

  for (const el of elements) {
    if (el.shadowRoot) {
      pieces.push(getReadableText(el.shadowRoot, seenRoots));
    }
    if (el.tagName === "IFRAME" || el.tagName === "FRAME") {
      const frameDocument = getSameOriginFrameDocument(el);
      if (frameDocument) {
        pieces.push(getReadableText(frameDocument, seenRoots));
      }
    }
  }

  return pieces.filter(Boolean).join("\n");
}

function queryAllElements(root = document, seenRoots = new Set()) {
  if (!root || seenRoots.has(root)) return [];
  seenRoots.add(root);

  let elements = [];
  try {
    elements = Array.from(root.querySelectorAll("*"));
  } catch (err) {
    return [];
  }

  for (const el of [...elements]) {
    if (el.shadowRoot) {
      elements.push(...queryAllElements(el.shadowRoot, seenRoots));
    }

    if (el.tagName === "IFRAME" || el.tagName === "FRAME") {
      const frameDocument = getSameOriginFrameDocument(el);
      if (frameDocument) {
        elements.push(...queryAllElements(frameDocument, seenRoots));
      }
    }
  }

  return elements;
}

function findElement(selector) {
  const direct = document.querySelector(selector);
  if (direct) return direct;

  for (const el of queryAllElements()) {
    try {
      if (typeof el.matches === "function" && el.matches(selector)) {
        return el;
      }
    } catch (err) {
      return null;
    }
  }
  return null;
}

function getInteractiveDOM() {
  const interactiveTags = ["BUTTON", "A", "INPUT", "SELECT", "TEXTAREA", "SUMMARY"];
  const interactiveRoles = ["button", "link", "checkbox", "tab", "menuitem", "option", "combobox", "textbox", "radio", "switch"];

  const allElements = queryAllElements();
  const interactiveList = [];
  let agentIdCounter = 1;

  for (const el of allElements) {
    if (interactiveList.length >= DOM_ELEMENT_BUDGET) break;
    const tagName = el.tagName;
    const role = el.getAttribute("role");
    const hasOnClick = el.hasAttribute("onclick") || typeof el.onclick === "function";
    const isContentEditable = el.isContentEditable || el.getAttribute("contenteditable") === "true";
    
    let isInteractive = interactiveTags.includes(tagName) ||
                        interactiveRoles.includes(role) ||
                        hasOnClick ||
                        isContentEditable;
                        
    // Exclude hidden inputs
    if (tagName === "INPUT" && el.type === "hidden") {
      isInteractive = false;
    }
    
    if (isInteractive) {
      const rect = el.getBoundingClientRect();
                        
      if (isVisibleElement(el)) {
        el.setAttribute("data-agent-id", agentIdCounter);
        
        const text = cleanText(el.innerText || el.textContent, 140);
        const label = getLabelText(el);
        const options = tagName === "SELECT"
          ? Array.from(el.options).map((option) => ({
              text: cleanText(option.textContent, 80),
              value: option.value,
              selected: option.selected
            })).slice(0, 30)
          : null;
        
        interactiveList.push({
          id: agentIdCounter,
          tagName: tagName,
          type: el.type || null,
          text: text,
          label: label,
          ariaLabel: el.getAttribute("aria-label") || null,
          title: el.getAttribute("title") || null,
          name: el.getAttribute("name") || null,
          elementId: el.getAttribute("id") || null,
          className: cleanText(el.getAttribute("class"), 120) || null,
          role: role || null,
          placeholder: el.getAttribute("placeholder") || null,
          value: el.value || null,
          selector: `[data-agent-id="${agentIdCounter}"]`,
          fingerprint: getElementFingerprint(el),
          disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
          checked: el.checked || false,
          href: el.getAttribute("href") || null,
          formAction: el.form ? el.form.getAttribute("action") : null,
          options: options,
          rect: {
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            width: Math.round(rect.width),
            height: Math.round(rect.height)
          },
          inViewport: isInViewport(rect)
        });
        
        agentIdCounter++;
      }
    }
  }


  return interactiveList;
}

function getPageText() {
  const text = cleanText(getReadableText(document), 6000);
  return {
    title: document.title,
    url: window.location.href,
    text: text
  };
}

function setNativeValue(element, value) {
  const proto = element instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
  if (descriptor && descriptor.set) {
    descriptor.set.call(element, value);
  } else {
    element.value = value;
  }
}

function dispatchKey(key) {
  const target = document.activeElement || document.body;
  const eventInit = { key, code: key, bubbles: true, cancelable: true };
  target.dispatchEvent(new KeyboardEvent("keydown", eventInit));
  target.dispatchEvent(new KeyboardEvent("keypress", eventInit));
  target.dispatchEvent(new KeyboardEvent("keyup", eventInit));
}

function getElementCenter(element) {
  const rect = element.getBoundingClientRect();
  return {
    x: Math.round(rect.left + rect.width / 2),
    y: Math.round(rect.top + rect.height / 2)
  };
}

function dispatchPointerMouseEvent(element, type, point) {
  const eventInit = {
    bubbles: true,
    cancelable: true,
    composed: true,
    view: window,
    clientX: point.x,
    clientY: point.y,
    pointerId: 1,
    pointerType: "mouse",
    isPrimary: true,
    button: 0,
    buttons: type === "pointerup" || type === "mouseup" || type === "click" ? 0 : 1
  };

  if (type.startsWith("pointer")) {
    element.dispatchEvent(new PointerEvent(type, eventInit));
  } else {
    element.dispatchEvent(new MouseEvent(type, eventInit));
  }
}

function prepareElementForAction(element) {
  if (typeof element.scrollIntoView === "function") {
    element.scrollIntoView({ block: "center", inline: "center", behavior: "auto" });
  }
  if (typeof element.focus === "function") {
    element.focus({ preventScroll: true });
  }
}

function executePageAction(action, selector, value, expectedFingerprint) {
  if (action === "scroll") {
    if (value === "down") window.scrollBy({ top: window.innerHeight * 0.7, behavior: "smooth" });
    else if (value === "up") window.scrollBy({ top: -window.innerHeight * 0.7, behavior: "smooth" });
    else if (value === "top") window.scrollTo({ top: 0, behavior: "smooth" });
    else if (value === "bottom") window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
    else throw new Error(`Invalid scroll direction: ${value}`);
    return;
  }

  if (action === "navigate") {
    window.location.href = value;
    return;
  }

  if (action === "back") {
    window.history.back();
    return;
  }

  if (action === "forward") {
    window.history.forward();
    return;
  }

  if (action === "reload") {
    window.location.reload();
    return;
  }

  if (action === "key") {
    dispatchKey(value);
    return;
  }

  if (action === "wait") {
    return;
  }

  if (action === "get_text") {
    return;
  }

  const element = findElement(selector);
  if (!element) {
    throw new Error(`Element not found with selector: ${selector}`);
  }

  if (expectedFingerprint && getElementFingerprint(element) !== expectedFingerprint) {
    throw new Error(`Element fingerprint changed before action: ${selector}`);
  }

  if (element.disabled || element.getAttribute("aria-disabled") === "true") {
    throw new Error(`Element is disabled: ${selector}`);
  }

  prepareElementForAction(element);

  if (action === "click") {
    const center = getElementCenter(element);
    dispatchPointerMouseEvent(element, "pointerover", center);
    dispatchPointerMouseEvent(element, "mouseover", center);
    dispatchPointerMouseEvent(element, "pointerdown", center);
    dispatchPointerMouseEvent(element, "mousedown", center);
    dispatchPointerMouseEvent(element, "pointerup", center);
    dispatchPointerMouseEvent(element, "mouseup", center);
    element.click();
    if (element.tagName === "INPUT" && (element.type === "checkbox" || element.type === "radio")) {
      element.dispatchEvent(new Event("change", { bubbles: true }));
    }
  } else if (action === "input") {
    if (element.isContentEditable) {
      element.textContent = value;
    } else {
      setNativeValue(element, value);
    }
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  } else if (action === "select") {
    if (element.tagName !== "SELECT") {
      throw new Error(`Element is not a SELECT: ${selector}`);
    }
    element.value = value;
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  } else if (action === "hover") {
    const center = getElementCenter(element);
    dispatchPointerMouseEvent(element, "pointerover", center);
    dispatchPointerMouseEvent(element, "mouseover", center);
    element.dispatchEvent(new MouseEvent("mouseenter", { bubbles: true, cancelable: true, composed: true, view: window, clientX: center.x, clientY: center.y }));
  } else {
    throw new Error(`Unsupported action: ${action}`);
  }
}

// Listen for action executions and DOM extraction requests from the extension sidepanel
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  console.log("Content script received message:", request);
  
  if (request.type === 'get_dom') {
    try {
      const dom = getInteractiveDOM();
      sendResponse({ status: 'success', dom_tree: dom, page_text: getPageText() });
    } catch (e) {
      sendResponse({ status: 'error', error: e.message });
    }
  } else if (request.type === 'get_page_text') {
    try {
      sendResponse({ status: "success", page_text: getPageText(), dom_tree: getInteractiveDOM() });
    } catch (e) {
      sendResponse({ status: "error", error: e.message });
    }
  } else if (request.type === 'execute_action') {
    const { action, selector, value, expected_fingerprint: expectedFingerprint } = request;
    try {
      executePageAction(action, selector, value, expectedFingerprint);
      
      setTimeout(() => {
        try {
          const dom = getInteractiveDOM();
          sendResponse({ status: 'success', dom_tree: dom, page_text: action === "get_text" ? getPageText() : null });
        } catch (domErr) {
          sendResponse({ status: 'error', error: domErr.message });
        }
      }, PAGE_SETTLE_DELAY_MS);
      
    } catch (e) {
      sendResponse({ status: 'error', error: e.message });
    }
  }
  return true; // Retain message channel open for asynchronous sendResponse
});
}
