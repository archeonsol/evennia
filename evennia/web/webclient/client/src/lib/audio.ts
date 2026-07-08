// Tiny synthesized UI sounds - no assets. A short square-wave click for the
// keyboard-SFX setting; the AudioContext is created lazily on first use (browsers
// require a user gesture, which typing satisfies).

let ctx: AudioContext | null = null;

function context(): AudioContext | null {
  try {
    if (!ctx) {
      const AC = window.AudioContext || (window as any).webkitAudioContext;
      ctx = new AC();
    }
    return ctx;
  } catch {
    return null;
  }
}

export function playMention(): void {
  const ac = context();
  if (!ac) return;
  try {
    const now = ac.currentTime;
    [660, 990].forEach((f, i) => {
      const osc = ac.createOscillator();
      const gain = ac.createGain();
      osc.type = "sine";
      osc.frequency.value = f;
      const t = now + i * 0.09;
      gain.gain.setValueAtTime(0.05, t);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.13);
      osc.connect(gain);
      gain.connect(ac.destination);
      osc.start(t);
      osc.stop(t + 0.14);
    });
  } catch {
    /* ignore */
  }
}

export function playKey(): void {
  const ac = context();
  if (!ac) return;
  try {
    const now = ac.currentTime;
    const osc = ac.createOscillator();
    const gain = ac.createGain();
    osc.type = "square";
    osc.frequency.value = 180 + Math.random() * 70;
    gain.gain.setValueAtTime(0.035, now);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.045);
    osc.connect(gain);
    gain.connect(ac.destination);
    osc.start(now);
    osc.stop(now + 0.05);
  } catch {
    /* ignore */
  }
}
