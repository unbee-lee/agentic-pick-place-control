// Speech edits the field only. It has no access to workflow submission functions.
function setupSpeech({input, form, context, canApply}) {
  const start = document.querySelector('[data-testid="speech-start"]');
  const stop = document.querySelector('[data-testid="speech-stop"]');
  const status = document.querySelector('[data-testid="speech-status"]');
  let state = 'idle', revision = 0, generation = 0;
  let stream, audio, source, node, timer;
  let chunks = [], frames = 0, rate = 0, baseline;
  let stopResolve;
  input.addEventListener('input', () => revision++);
  form.addEventListener('submit', () => revision++, true);
  form.querySelector('[data-action="cancel"]').addEventListener('click', () => revision++);
  function show(next, message) {
    state = next;
    status.textContent = message;
    status.dataset.state = next;
    start.disabled = next !== 'idle';
    stop.disabled = !['permission', 'recording', 'limit'].includes(next);
    stop.textContent = next === 'permission' ? 'Cancel microphone' : 'Stop microphone';
  }
  function release() {
    clearTimeout(timer);
    stream?.getTracks().forEach(track => track.stop());
    source?.disconnect();
    node?.disconnect();
    if (audio && audio.state !== 'closed') void audio.close();
    stream = audio = source = node = null;
  }
  function wav() {
    const buffer = new ArrayBuffer(44 + frames * 2);
    const view = new DataView(buffer);
    function label(offset, value) {
      for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
    }
    label(0, 'RIFF'); view.setUint32(4, buffer.byteLength - 8, true); label(8, 'WAVE');
    label(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
    view.setUint16(22, 1, true); view.setUint32(24, rate, true);
    view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    label(36, 'data'); view.setUint32(40, frames * 2, true);
    let offset = 44;
    for (const chunk of chunks) for (const value of chunk) {
      const sample = Math.max(-1, Math.min(1, value));
      view.setInt16(offset, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
      offset += 2;
    }
    return buffer;
  }
  start.addEventListener('click', async () => {
    if (state !== 'idle') return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) {
      show('idle', 'Microphone recording needs a supported browser on localhost or HTTPS. You can still type.');
      return;
    }
    const attempt = ++generation;
    baseline = {revision, value: input.value, context: context()};
    chunks = []; frames = 0;
    show('permission', 'Waiting for microphone permission…');
    try {
      // Resume within the user gesture for browsers with audio autoplay restrictions.
      audio = new AudioContext({sampleRate: 16000});
      await audio.resume();
      if (attempt !== generation) return;
      const captured = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1}, video: false});
      if (attempt !== generation) {
        captured.getTracks().forEach(track => track.stop());
        return;
      }
      stream = captured;
      rate = audio.sampleRate;
      if (rate < 8000 || rate > 48000) throw new Error('unsupported');
      await audio.audioWorklet.addModule('/speech/recorder.js');
      if (attempt !== generation) return;
      source = audio.createMediaStreamSource(stream);
      node = new AudioWorkletNode(audio, 'pcm-recorder', {channelCount: 1, channelCountMode: 'explicit'});
      node.port.onmessage = ({data}) => {
        if (attempt !== generation) return;
        if (data.chunk) { chunks.push(data.chunk); frames += data.chunk.length; }
        if (data.stopped) stopResolve?.();
        if (data.limit && state === 'recording') {
          release();
          show('limit', '30-second limit reached. Press Stop microphone to transcribe this recording.');
        }
      };
      source.connect(node);
      // The worklet outputs silence; connecting keeps processing active without feedback.
      node.connect(audio.destination);
      stream.getAudioTracks()[0].onended = () => {
        if (state === 'recording') {
          ++generation; release(); chunks = [];
          show('idle', 'Microphone disconnected. Reconnect it and try again, or type your request.');
        }
      };
      show('recording', 'Recording… Press Stop microphone to send the audio to Google (maximum 30 seconds).');
    } catch (error) {
      if (attempt !== generation) return;
      release(); chunks = [];
      const messages = {
        NotAllowedError: 'Microphone permission was denied. Allow microphone access in your browser, or type your request.',
        NotFoundError: 'No microphone was found. Connect a microphone, or type your request.',
        NotReadableError: 'The microphone is unavailable or in use. Check your device, or type your request.',
      };
      show('idle', messages[error.name] || 'Microphone recording is unavailable. Try another browser or type your request.');
    }
  });
  stop.addEventListener('click', async () => {
    if (state === 'permission') {
      ++generation; release(); chunks = [];
      show('idle', 'Microphone request cancelled. Nothing was sent.');
      return;
    }
    if (!['recording', 'limit'].includes(state)) return;
    show('transcribing', 'Transcribing with Google… You can keep editing your request.');
    try {
      if (node) {
        // Acknowledgement flushes every earlier audio chunk before WAV encoding.
        await new Promise((resolve, reject) => {
          stopResolve = resolve;
          timer = setTimeout(() => reject(new Error('Recording could not be stopped. Please try again.')), 2000);
          node.port.postMessage('stop');
        });
      }
      release();
      const body = wav(); chunks = [];
      const controller = new AbortController();
      timer = setTimeout(() => controller.abort(), 27000);
      const response = await fetch('/speech/transcriptions', {
        method: 'POST', headers: {'Content-Type': 'audio/wav'}, body, signal: controller.signal,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Transcription failed. Please try again.');
      if (typeof result.transcript !== 'string' || !result.transcript.trim() || result.transcript.length > 4000) {
        throw new Error('No usable transcript was returned. Please try again or type your request.');
      }
      if (revision !== baseline.revision || input.value !== baseline.value || context() !== baseline.context || !canApply()) {
        show('idle', 'Your input or request changed. The late transcript was not inserted. Record again if needed.');
      } else if ((input.value + ' ' + result.transcript).trim().length > 4000) {
        show('idle', 'The combined request is too long. Shorten your text and record again.');
      } else {
        input.value = [input.value.trim(), result.transcript.trim()].filter(Boolean).join(' ');
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.focus();
        show('idle', 'Transcript added. Review and edit it, then explicitly submit when ready. Nothing has been submitted.');
      }
    } catch (error) {
      show('idle', error.name === 'AbortError' ? 'Transcription timed out. Try again or type your request.' :
        error instanceof TypeError ? 'Cannot reach transcription. Check your connection or type your request.' : error.message);
    } finally {
      release(); chunks = []; stopResolve = null;
    }
  });
  window.addEventListener('pagehide', () => { ++generation; release(); chunks = []; });
}
