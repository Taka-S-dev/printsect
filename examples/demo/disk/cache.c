#include "disk.h"
#include "ring.h"

static ring_t cache;

int cache_put(int lba)
{
    return ring_push(&cache, lba);
}
