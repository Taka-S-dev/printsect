# Included by each module after it sets MODULE and OBJS.
include $(dir $(lastword $(MAKEFILE_LIST)))config.mk

OBJDIR = $(ROOT)/build/obj/$(MODULE)

$(LIBDIR)/lib$(MODULE).a: $(addprefix $(OBJDIR)/,$(OBJS))
	@mkdir -p $(LIBDIR)
	$(AR) crus $@ $^

$(OBJDIR)/%.o: %.c
	@mkdir -p $(OBJDIR)
	$(CC) $(CFLAGS) $(EXTRA_CFLAGS) -DMODULE=\"$(MODULE)\" -c $< -o $@
