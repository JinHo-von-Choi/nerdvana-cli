'use strict';

/** A small synchronous event emitter. */
class Emitter {
  constructor() {
    this.listeners = new Map();
  }

  /** Register a listener for event; returns this. */
  on(event, fn) {
    if (!this.listeners.has(event)) this.listeners.set(event, []);
    this.listeners.get(event).push(fn);
    return this;
  }

  /** Register a listener that runs on the next emit only; returns this. */
  once(event, fn) {
    const wrapper = (...args) => {
      this.off(event, wrapper);
      fn(...args);
    };
    wrapper.original = fn;
    return this.on(event, wrapper);
  }

  /** Remove one registration of fn (plain or once) for event; returns this. */
  off(event, fn) {
    const list = this.listeners.get(event);
    if (!list) return this;
    const index = list.findIndex((item) => item === fn || item.original === fn);
    if (index !== -1) list.splice(index, 1);
    return this;
  }

  /** Call the listeners of event in registration order; returns how many were called. */
  emit(event, ...args) {
    const snapshot = [...(this.listeners.get(event) || [])];
    for (const fn of snapshot) fn(...args);
    return snapshot.length;
  }
}

module.exports = { Emitter };
