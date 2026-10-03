#include <stdio.h>
#include <stdlib.h>
#include "ring.h"

#define CHECK(cond) do { if (!(cond)) { fprintf(stderr, "failed: %s (line %d)\n", #cond, __LINE__); exit(1); } } while (0)

int main(void) {
    ring_t r;
    int out = -1;
    rb_init(&r);
    CHECK(rb_pop(&r, &out) == -1);
    for (int i = 1; i <= RB_CAP; i++) {
        CHECK(rb_push(&r, i) == 0);
    }
    CHECK(rb_push(&r, 99) == -1);
    CHECK(r.count == RB_CAP);
    CHECK(rb_pop(&r, &out) == 0 && out == 1);
    CHECK(rb_push(&r, 5) == 0);
    CHECK(rb_push(&r, 6) == -1);
    for (int i = 2; i <= 5; i++) {
        CHECK(rb_pop(&r, &out) == 0 && out == i);
    }
    CHECK(rb_pop(&r, &out) == -1);
    CHECK(r.count == 0);
    puts("ok");
    return 0;
}
