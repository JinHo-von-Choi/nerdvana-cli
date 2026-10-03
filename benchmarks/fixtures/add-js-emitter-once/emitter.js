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

  /** Call the listeners of event in registration order; returns how many were called. */
  emit(event, ...args) {
    const list = this.listeners.get(event) || [];
    for (const fn of [...list]) fn(...args);
    return list.length;
  }
}

module.exports = { Emitter };
