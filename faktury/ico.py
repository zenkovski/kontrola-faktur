"""Czech company ID (IČO) check digit, modulo 11."""


def ico_valid(ico: str) -> bool:
    if not ico.isdigit() or len(ico) > 8:
        return False
    ico = ico.zfill(8)
    s = sum(int(ico[i]) * (8 - i) for i in range(7))
    check = (11 - s % 11) % 10
    return check == int(ico[7])
