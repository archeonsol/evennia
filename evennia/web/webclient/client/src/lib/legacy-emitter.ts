export type LegacyListener = (args: any[], kwargs: Record<string, any>) => void;

export interface LegacyEmitter {
  emit(event: string, args?: any[], kwargs?: Record<string, any>): void;
  on(event: string, listener: LegacyListener): void;
  off(event: string): void;
}

/** Minimal Evennia emitter surface for progressively enhanced legacy plugins. */
export function createLegacyEmitter(): LegacyEmitter {
  const listeners = new Map<string, LegacyListener>();
  return {
    emit(event, args = [], kwargs = {}) {
      listeners.get(event)?.(args, kwargs);
    },
    on(event, listener) {
      listeners.set(event, listener);
    },
    off(event) {
      listeners.delete(event);
    },
  };
}
