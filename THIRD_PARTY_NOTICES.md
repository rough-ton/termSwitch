# Third-party notices

termSwitch itself is released under the MIT license; see [LICENSE](LICENSE). The
downloadable builds also contain the third-party software listed here, each under its
own license. Full texts of the longer licenses are in the [licenses](licenses) folder.
In the app, open **Help > Licenses** to read all of them.

## Summary

Versions are the ones pinned in `requirements.lock` and `requirements-build.lock`.

| Package | Version | License | Source |
| --- | --- | --- | --- |
| `configupdater` | 4.0 | MIT | https://github.com/pyscaffold/configupdater |
| `jaraco-classes` | 3.4.0 | MIT | https://github.com/jaraco/jaraco.classes |
| `jaraco-context` | 6.1.2 | MIT | https://github.com/jaraco/jaraco.context |
| `jaraco-functools` | 4.6.0 | MIT | https://github.com/jaraco/jaraco.functools |
| `keyring` | 25.7.0 | MIT | https://github.com/jaraco/keyring |
| `more-itertools` | 11.2.1 | MIT | https://github.com/more-itertools/more-itertools |
| `ptyprocess` | 0.7.0 | ISC | https://github.com/pexpect/ptyprocess |
| `pyside6-essentials` | 6.12.0 | LGPL-3.0-only | https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| `pyte` | 0.8.2 | LGPL-3.0-only | https://github.com/selectel/pyte |
| `pywin32-ctypes` | 0.2.3 | BSD-3-Clause | https://github.com/enthought/pywin32-ctypes |
| `pywinpty` | 3.1.0 | MIT | https://github.com/andfoy/pywinpty |
| `shiboken6` | 6.12.0 | LGPL-3.0-only | https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| `wcwidth` | 0.9.3 | MIT | https://github.com/jquast/wcwidth |
| `pyinstaller` (bootloader and runtime only) | 6.22.3 | GPL-2.0-or-later with the Bootloader Exception; runtime hooks Apache-2.0 | https://github.com/pyinstaller/pyinstaller |
| Python runtime | 3.13 | PSF-2.0 | https://www.python.org/downloads/source/ |
| Material Icon Theme (icons) | `13025ad` | MIT | https://github.com/material-extensions/vscode-material-icon-theme |
| Terminal color schemes: Dracula, Catppuccin, Solarized, Nord, Gruvbox (colors only) | n/a | MIT | see [Terminal color schemes](#terminal-color-schemes) |

`ptyprocess` is in the macOS build only. `keyring` and the `jaraco` and
`more-itertools` packages it uses are in the macOS and Windows builds, for saving SSH
passwords in the system password store; `pywin32-ctypes` is in the Windows build only. `pywinpty`, with the winpty and Windows ConPTY
components it ships, is in the Windows build only.

## Qt for Python (PySide6-Essentials and shiboken6)

termSwitch uses Qt 6.12.0 through Qt for Python (PySide6-Essentials and shiboken6).
Qt and Qt for Python are copyright The Qt Company Ltd. and other contributors, and are
used here under the **GNU Lesser General Public License version 3** (LGPL-3.0). The LGPL
and the GNU GPL it builds on are included as [licenses/LGPL-3.0.txt](licenses/LGPL-3.0.txt)
and [licenses/GPL-3.0.txt](licenses/GPL-3.0.txt).

- termSwitch does not modify Qt or Qt for Python. The builds contain the unmodified
  binaries published on PyPI as `PySide6-Essentials` and `shiboken6` 6.12.0.
- Source code for Qt for Python: https://code.qt.io/cgit/pyside/pyside-setup.git/ (tag
  `v6.12.0`). Source code for Qt itself: https://download.qt.io/official_releases/qt/6.12/
- Qt includes third-party components under their own licenses, listed at
  https://doc.qt.io/qt-6/licenses-used-in-qt.html
- You may replace the Qt libraries with a modified, interface-compatible version. In the
  macOS app they are separate frameworks inside `termSwitch.app/Contents/Frameworks`.
  For either platform, the complete termSwitch source and its PyInstaller build spec
  (`termswitch.spec`) let you run termSwitch against, or rebuild it with, any PySide6
  build you choose.

## pyte

pyte 0.8.2 is copyright (c) 2011-2012 Selectel and (c) 2012-2017 the pyte authors and
contributors, and is used under the **GNU Lesser General Public License version 3**;
see [licenses/LGPL-3.0.txt](licenses/LGPL-3.0.txt) and
[licenses/GPL-3.0.txt](licenses/GPL-3.0.txt).
termSwitch uses an unmodified copy. Source: https://github.com/selectel/pyte (tag
`0.8.2`) and https://pypi.org/project/pyte/0.8.2/. As with Qt, you can run or rebuild
termSwitch from source with a modified pyte.

## configupdater

```text
The MIT License (MIT)

Copyright (c) 2018 Florian Wilhelm

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

ConfigUpdater includes code derived from the Python standard library, covered by the
Python Software Foundation License described under "Python runtime" below.

## wcwidth

```text
The MIT License (MIT)

Copyright (c) 2014 Jeff Quast <contact@jeffquast.com>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Markus Kuhn -- 2007-05-26 (Unicode 5.0)

Permission to use, copy, modify, and distribute this software
for any purpose and without fee is hereby granted. The author
disclaims all warranties with regard to this software.
```

## keyring (macOS and Windows builds)

Used only to save and read SSH passwords in the macOS Keychain or the Windows
Credential Manager. https://github.com/jaraco/keyring, by Jason R. Coombs and
contributors.

```text
MIT License

Copyright (c) 2025 <copyright holders>

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the
following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO
EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
USE OR OTHER DEALINGS IN THE SOFTWARE.
```

### jaraco.classes, jaraco.context, jaraco.functools

Used by keyring. By Jason R. Coombs and contributors.

jaraco.classes:

```text
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to
deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
IN THE SOFTWARE.
```

jaraco.context:

```text
MIT License

Copyright (c) 2026 <copyright holders>

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the
following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO
EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
USE OR OTHER DEALINGS IN THE SOFTWARE.
```

jaraco.functools:

```text
MIT License

Copyright (c) 2026 <copyright holders>

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the
following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO
EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
USE OR OTHER DEALINGS IN THE SOFTWARE.
```

### more-itertools

Used by keyring.

```text
Copyright (c) 2012 Erik Rose

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### pywin32-ctypes (Windows build)

Used by keyring to reach the Windows Credential Manager.

```text
This software is OSI Certified Open Source Software.
OSI Certified is a certification mark of the Open Source Initiative.

Copyright (c) 2014, Enthought, Inc.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

 * Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
 * Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
 * Neither the name of Enthought, Inc. nor the names of its contributors may
   be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## ptyprocess (macOS build)

```text
Ptyprocess is under the ISC license, as code derived from Pexpect.
    http://opensource.org/licenses/ISC

Copyright (c) 2013-2014, Pexpect development team
Copyright (c) 2012, Noah Spurrier <noah@noah.org>

PERMISSION TO USE, COPY, MODIFY, AND/OR DISTRIBUTE THIS SOFTWARE FOR ANY PURPOSE
WITH OR WITHOUT FEE IS HEREBY GRANTED, PROVIDED THAT THE ABOVE COPYRIGHT NOTICE
AND THIS PERMISSION NOTICE APPEAR IN ALL COPIES. THE SOFTWARE IS PROVIDED
"AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE
INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT
SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL
DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS,
WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING
OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

## pywinpty (Windows build)

```text
MIT License

Copyright (c) 2017 Spyder IDE

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

pywinpty ships these components, which are included in the Windows build:

**winpty** (`winpty.dll`, `winpty-agent.exe`), https://github.com/rprichard/winpty

```text
The MIT License (MIT)

Copyright (c) 2011-2016 Ryan Prichard

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to
deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
IN THE SOFTWARE.
```

**Windows Terminal ConPTY** (`conpty.dll`, `OpenConsole.exe`), https://github.com/microsoft/terminal

```text
Copyright (c) Microsoft Corporation. All rights reserved.

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED *AS IS*, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## PyInstaller

The builds are made with PyInstaller 6.22.3, which embeds its bootloader and a few
runtime files in the app. PyInstaller is licensed under the GNU GPL version 2 or later
([licenses/GPL-2.0.txt](licenses/GPL-2.0.txt)) with this exception, which allows the
built app to be distributed under any license:

```text
Bootloader Exception
--------------------

In addition to the permissions in the GNU General Public License, the
authors give you unlimited permission to link or embed compiled bootloader
and related files into combinations with other programs, and to distribute
those combinations without any restriction coming from the use of those
files. (The General Public License restrictions do apply in other respects;
for example, they cover modification of the files, and distribution when
not linked into a combined executable.)
```

PyInstaller's run-time hooks and run-time modules embedded in the app are licensed under
the Apache License 2.0 ([licenses/Apache-2.0.txt](licenses/Apache-2.0.txt)).
Copyright (c) 2010-2023, PyInstaller Development Team; Copyright (c) 2005-2009,
Giovanni Bajo; based on previous work under copyright (c) 2002 McMillan Enterprises, Inc.

## Python runtime

The builds include the Python 3.13 interpreter and standard library, copyright (c)
2001-present Python Software Foundation, used under the Python Software Foundation
License Version 2. The license of the exact interpreter used is bundled with the app and
shown in Help > Licenses. Python also incorporates third-party software, including
OpenSSL (Apache-2.0), libffi, zlib, Expat, bzip2, liblzma and libmpdec, whose notices are
listed at https://docs.python.org/3.13/license.html

## Material Icon Theme

Icons in `termswitch/assets/icons/material/` come from the Material Icon Theme,
created by Philipp Kief and maintained by Material Extensions.

- Source: https://github.com/material-extensions/vscode-material-icon-theme
- Version: commit `13025adc5ad10a5cc649528a2c1edec93069f1b9`
- License: MIT, reproduced below and in `termswitch/assets/icons/material/LICENSE`

The AWS and SSH icons in `termswitch/assets/icons/termswitch/` are termSwitch's own,
drawn in the same style: `aws.svg` uses the cloud shape from the theme's `folder-aws`
icon, and `ssh.svg` the terminal from its `console` icon, each recolored. They are
covered by the same MIT license. Neither is an AWS logo. The "AWS" lettering in
`aws.svg` is outlines of DejaVu Sans Condensed Bold glyphs; see "DejaVu fonts" below.

Some icons depict third-party brands, such as AWS, PowerShell, Docker, Kubernetes,
Git and GitHub. Those marks belong to
their owners and are used only to identify the related technology. The MIT license
covers the icon artwork and grants no trademark rights.

```text
The MIT License (MIT)
Copyright (c) 2025 Material Extensions

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## DejaVu fonts

The "AWS" lettering in `termswitch/assets/icons/termswitch/aws.svg` is drawn from
DejaVu Sans Condensed Bold glyph outlines. No font file is included.

- Source: https://dejavu-fonts.github.io/
- License: Bitstream Vera Fonts license; DejaVu changes are in the public domain.

```text
Copyright (c) 2003 by Bitstream, Inc. All Rights Reserved. Bitstream Vera is a
trademark of Bitstream, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy of the
fonts accompanying this license ("Fonts") and associated documentation files (the
"Font Software"), to reproduce and distribute the Font Software, including without
limitation the rights to use, copy, merge, publish, distribute, and/or sell copies of
the Font Software, and to permit persons to whom the Font Software is furnished to do
so, subject to the following conditions:

The above copyright and trademark notices and this permission notice shall be
included in all copies of one or more of the Font Software typefaces.

The Font Software may be modified, altered, or added to, and in particular the
designs of glyphs or characters in the Fonts may be modified and additional glyphs or
characters may be added to the Fonts, only if the fonts are renamed to names not
containing either the words "Bitstream" or the word "Vera".

This License becomes null and void to the extent applicable to Fonts or Font Software
that has been modified and is distributed under the "Bitstream Vera" names.

The Font Software may be sold as part of a larger software package but no copy of one
or more of the Font Software typefaces may be sold by itself.

THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
PARTICULAR PURPOSE AND NONINFRINGEMENT OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER
RIGHT. IN NO EVENT SHALL BITSTREAM OR THE GNOME FOUNDATION BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR
CONSEQUENTIAL DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM OTHER DEALINGS IN
THE FONT SOFTWARE.

Except as contained in this notice, the names of Gnome, the Gnome Foundation, and
Bitstream Inc., shall not be used in advertising or otherwise to promote the sale, use
or other dealings in this Font Software without prior written authorization from the
Gnome Foundation or Bitstream Inc., respectively. For further information, contact:
fonts at gnome dot org.
```

## Terminal color schemes

The built-in terminal schemes are termSwitch's own, informed by these color sets:

- **Dark** starts from the "Campbell" scheme of Windows Terminal
  (https://github.com/microsoft/terminal, MIT, Copyright (c) Microsoft Corporation),
  with lighter blue and magenta.
- **Light** uses colors from GitHub's Primer design system
  (https://github.com/primer/primitives, MIT, Copyright (c) 2018 GitHub Inc.).

Both licenses are MIT; the full MIT text appears above with the Windows Terminal
components.

For **Match my system terminal**, termSwitch includes the colors of Windows Terminal's
built-in schemes (Campbell, One Half Dark and the rest), from
`src/cascadia/TerminalSettingsModel/defaults.json` in https://github.com/microsoft/terminal
(MIT, Copyright (c) Microsoft Corporation; the MIT text appears above).

termSwitch also bundles the published colors of these schemes, unchanged. Only the
color values are used; no code or other files from these projects are included.

- **Dracula:** from https://github.com/dracula/windows-terminal.
  Copyright (c) 2019 thismat.
- **Catppuccin Mocha and Catppuccin Latte:** from
  https://github.com/catppuccin/windows-terminal. Copyright (c) 2021 Catppuccin.
- **Solarized Dark and Solarized Light:** the palette and terminal color mapping from
  https://github.com/altercation/solarized. Copyright (c) 2011 Ethan Schoonover.
- **Nord:** the palette from https://github.com/nordtheme/nord, mapped to terminal
  colors as in Nord's terminal ports. Copyright (c) 2016-present Sven Greb.
- **Gruvbox Dark:** the palette from https://github.com/morhetz/gruvbox, by morhetz,
  released under the MIT/X11 license.

Each is released under the MIT license:

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
