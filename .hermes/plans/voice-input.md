# Voice Input Feature Plan

## Goal
Add on-device STT (speech-to-text) to Hermes Proxy chat input, with a full-width voice visualizer bar under the composer.

## Architecture
- **Zero backend changes.** Pure browser feature using `SpeechRecognition` with `processLocally = true`.
- **Chrome 139+ required.** On unsupported browsers, the mic button is hidden completely.
- **Language toggle:** French (`fr-FR`) default, English (`en-US`) via toggle inside the visualizer area.
- **Text handling:** Append interim results to textarea (SpeechRecognition appends naturally).
- **Error display:** Visualizer area shows permission/no-speech errors as text.
- **Layout:** Same on mobile and desktop.

## Files

### `static/index.html`
- Add mic button (`#mic-btn`) next to send button, same styling.
- Add full-width visualizer rectangle (`#voice-viz`) under input area.
- Add language toggle button inside visualizer area.
- Add inline CSS for visualizer styling.

### `static/app.js`
- Detect `window.SpeechRecognition` + `processLocally` support; hide mic if absent.
- Implement `startRecording()` / `stopRecording()`.
- Handle `result`, `error`, `end` events.
- On mobile, blur textarea to hide keyboard when mic clicked.
- Toggle button: `fr-FR` ↔ `en-US`.
- Visualizer area: show recording state, interim text, errors.

### `static/__plugins__/voice-visualizer.js` (plugin)
- Renders basic blobs/bars animation into a canvas inside `#voice-viz`.
- Hooks into `onstart`, `onresult`, `onend` via custom events dispatched by app.js.
- Plugin contract: listens for `voice:start`, `voice:result`, `voice:end`, `voice:amplitude`.

### `static/__plugins__/voice-visualizer.css` (plugin)
- Optional stylesheet for blob colors, positioning.

## UI Flow
1. Page loads → detect STT support → hide mic if unsupported.
2. Tap mic → textarea blurs (keyboard hides) → `#voice-viz` appears → mic turns red ⏹️.
3. Speak → text appends to textarea → blobs animate in visualizer.
4. Tap red stop → visualizer hides → mic returns → user presses send.
5. Permission denied → visualizer shows "Microphone access needed" briefly.

## Open Questions
- Should we add a setting to auto-hide the visualizer after inactivity? → v2.
- Should the language persist per session? → v2.

## Implementation Order
1. Write plan (this doc) → get approval.
2. Add DOM elements + CSS to `index.html`.
3. Add STT detection + recording logic to `app.js`.
4. Create `voice-visualizer.js` plugin with basic blob animation.
5. Test live in browser (Chrome 139+).
6. Commit + push.
