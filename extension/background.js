// background.js - Chrome Extension Service Worker

// Set sidepanel behavior to open on action click
chrome.runtime.onInstalled.addListener(() => {
  if (chrome.sidePanel && typeof chrome.sidePanel.setPanelBehavior === 'function') {
    chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })
      .catch((error) => console.error("Error setting sidepanel behavior:", error));
  }
});

// Listener for logging/debugging or handling coordination messages if necessary
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log("Background received message:", message);
  // Just in case, return true to signify async response if needed
  return false;
});
