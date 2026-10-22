export interface AudioCapture {
  stream: MediaStream;
  stop: () => void;
  onEnded?: () => void;
}

/** Feed the same audio mix to the recorder and Speech SDK; never play it to speakers. */
export async function captureAudio(source: string): Promise<AudioCapture> {
  const inputs: MediaStream[] = [];
  const nodes: AudioNode[] = [];
  let context: AudioContext | undefined;
  let output: MediaStream | undefined;
  let stopped = false;
  const ended = () => {
    if (!stopped) {
      capture.onEnded?.();
      capture.stop();
    }
  };
  const capture: AudioCapture = {
    get stream() {
      return output!;
    },
    stop() {
      if (stopped) return;
      stopped = true;
      for (const input of inputs) {
        for (const track of input.getTracks()) {
          track.removeEventListener('ended', ended);
          track.stop();
        }
      }
      output?.getTracks().forEach((track) => track.stop());
      nodes.forEach((node) => node.disconnect());
      if (context) void context.close().catch(() => {});
    },
  };
  try {
    if (source === 'browser-tab' || source === 'meeting-audio') {
      // Keep the sharing track alive until Stop; only audio enters the recording.
      const options: DisplayMediaStreamOptions & { systemAudio: string } = {
        video: true,
        audio: true,
        systemAudio: 'include',
      };
      const shared = await navigator.mediaDevices.getDisplayMedia(options);
      inputs.push(shared);
      if (!shared.getAudioTracks().length)
        throw new Error(
          'No meeting audio was shared. Select the meeting tab with Share tab audio, or Entire screen with Also share system audio for Teams/Zoom desktop. If audio sharing is unavailable, join the meeting in an Edge/Chrome tab.',
        );
      output = new MediaStream(shared.getAudioTracks());
    }
    if (source === 'microphone' || source === 'meeting-audio') {
      const microphone = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      inputs.push(microphone);
      if (!microphone.getAudioTracks().length) throw new Error('No microphone audio is available.');
      output = new MediaStream(microphone.getAudioTracks());
    }
    if (!output) throw new Error('Select a supported audio source.');
    if (source === 'meeting-audio') {
      context = new AudioContext();
      const destination = context.createMediaStreamDestination();
      destination.channelCount = 1;
      for (const input of inputs) {
        const node = context.createMediaStreamSource(new MediaStream(input.getAudioTracks()));
        const gain = context.createGain();
        gain.gain.value = 0.5;
        node.connect(gain).connect(destination);
        nodes.push(node, gain);
      }
      output = destination.stream;
      await context.resume();
      if (context.state !== 'running')
        throw new Error('Audio mixing could not start. Retry Start Meeting.');
    }
    for (const input of inputs) {
      for (const track of input.getTracks()) {
        if (track.readyState !== 'live')
          throw new Error('An audio source ended before recording started.');
        track.addEventListener('ended', ended);
      }
    }
    return capture;
  } catch (error) {
    capture.stop();
    throw error;
  }
}
