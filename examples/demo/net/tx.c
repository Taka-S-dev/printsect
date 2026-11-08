#include "net.h"

status_t net_tx(const unsigned char *buf, int len)
{
    (void)buf;
    return len > 0 ? 0 : -1;
}
