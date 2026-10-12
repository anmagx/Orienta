# Third-party notices

## Accela-inspired native pose filter

Orienta's `src/filters/accela_style.py` is an independent implementation
inspired by the rotational deadzone and adaptive-response concepts in the
Accela filter from [opentrack](https://github.com/opentrack/opentrack). It
does not include copied opentrack source code or reproduce its spline
parameters. The upstream reference implementation is
[`ftnoir_filter_accela.cpp`](https://github.com/opentrack/opentrack/blob/master/filter-accela/ftnoir_filter_accela.cpp).

The upstream source identifies this notice:

> Copyright (c) 2012-2016 Stanislaw Halik
>
> Permission to use, copy, modify, and/or distribute this software for any
> purpose with or without fee is hereby granted, provided that the above
> copyright notice and this permission notice appear in all copies.

The opentrack licensing file also applies its warranty disclaimer to project
modules; see
[`OPENTRACK-LICENSING.txt`](https://github.com/opentrack/opentrack/blob/master/OPENTRACK-LICENSING.txt).
