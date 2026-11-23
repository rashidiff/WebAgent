# Run targets and approvals

Each run uses the tab and window selected when the prompt is sent. The sidepanel shows that target throughout the run. Switching to another tab does not redirect actions, including navigation, scrolling, and keyboard input.

Approvals refer to the displayed target page. Reloading or navigating that page after approval invalidates the grant. Closing the target, moving it to another window, or losing access stops the run with an error; start a new run on the desired page. Navigation performed by the agent updates its bound document and allows the run to continue.

Screenshots can only be captured while the target is the active tab in its window. Background execution skips screenshots rather than capturing the unrelated visible page. Screenshots remain subject to the backend capture setting and payload limits.

Run the mocked Chrome API regressions with:

```sh
node --test extension/tests/bound_tab.test.cjs
```
