#ifndef RING_H
#define RING_H

#define RB_CAP 4

typedef struct {
    int data[RB_CAP];
    int head;  /* index of the oldest element */
    int tail;  /* index where the next element is written */
    int count; /* number of stored elements */
} ring_t;

void rb_init(ring_t *r);
/* Returns 0 on success, -1 when the buffer is full (nothing is stored then). */
int rb_push(ring_t *r, int value);
/* Returns 0 on success and stores the oldest element in *out, -1 when empty. */
int rb_pop(ring_t *r, int *out);

#endif
