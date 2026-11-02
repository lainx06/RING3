/* INV-5 / A6: a write to maint_notes needs confirmation; prints the result code the human's answer produced. */
#include "libu.h"
int main(void)
{
    u_puts("confirmw: writing maint_notes (prompt expected)\n");
    int r = u_obj_write(OBJ_MAINT_NOTES, "edited", 6);
    u_puts("confirmw: result=");
    u_putdec(r);
    u_puts("\n");
    return r == 6 ? 0 : r == -ECANCELED ? 2 : 1;
}
