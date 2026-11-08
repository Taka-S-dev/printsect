#include "net.h"
#include "ring.h"
#include "log.h"

static ring_t rxq;

status_t net_rx(const unsigned char *buf, int len)
{
    if (len <= 0) {
        log_put("rx: empty");
        return -1;
    }
    ring_push(&rxq, buf[0]);
    return 0;
}
