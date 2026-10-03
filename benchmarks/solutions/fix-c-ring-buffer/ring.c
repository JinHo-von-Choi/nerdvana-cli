#include "ring.h"

void rb_init(ring_t *r) {
    r->head  = 0;
    r->tail  = 0;
    r->count = 0;
}

int rb_push(ring_t *r, int value) {
    if (r->count >= RB_CAP) {
        return -1;
    }
    r->data[r->tail] = value;
    r->tail = (r->tail + 1) % RB_CAP;
    r->count++;
    return 0;
}

int rb_pop(ring_t *r, int *out) {
    if (r->count == 0) {
        return -1;
    }
    *out = r->data[r->head];
    r->head = (r->head + 1) % RB_CAP;
    r->count--;
    return 0;
}
