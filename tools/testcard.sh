#!/bin/sh
#
# Regenerate the streaming test card used by the vsync delay sweep.
#
# The card is the one the tearing was first seen on when streaming to the FPGA
# breakout: a circle over vertically fading bars, a horizontal bar along the
# bottom, and a black rectangle on the right.  It was never committed, so this
# rebuilds it rather than leaving the sweep dependent on a file that only exists
# on one machine.
#
# The content is chosen for what it does to a *late* host, not for looking nice.
# When the host falls behind, the renderer paints digit row R from the buffer
# half still holding row R-4, so what shows up is content from four rows away.
# Structure that varies vertically is what makes that visible: the circle's
# edges, the bottom bar and the top and bottom of the black rectangle all move
# or duplicate, while the bars alone would slide without any obvious seam.
#
# Writes test/testcard.png (for eyeballing) and test/testcard.seg (frame 0 of
# which is what the sweep streams).
#
set -e

here=$(cd "$(dirname "$0")" && pwd)
out="$here/../test"

# 624x480 is the grid, not the screen: 52 columns of 12 px by 30 rows of 16.
# video2seg.py wants exactly that, one source pixel per display pixel.
#
# Everything is written as a target segment intensity 0-15 and then encoded as
# 255*(t/15)^(1/2.2), because video2seg.py linearises the source before
# averaging.  Drawing the bars as a plain luma ramp instead put half the card at
# intensity 0 and a quarter at 1: over half the screen came out black, and a
# black segment that should be black looks the same whether it is stale or not,
# so most of the frame could not show tearing at all.  The fade also stops at
# 30% rather than reaching zero, for the same reason.
ffmpeg -y -v error -f lavfi -i color=c=black:s=624x480 -frames:v 1 -vf \
    "format=gray,geq=lum='if(gt(X\,500)*between(Y\,60\,300), 0, 255*pow(if(between(Y\,400\,440), 13, if(lt(hypot(X-300\,Y-180)\,95), 15, (floor(X/78)+1)*1.875*(1-0.7*Y/480)))/15\, 1/2.2))'" \
    "$out/testcard.png"

cd "$here"
python3 video2seg.py "$out/testcard.png" "$out/testcard.seg"

echo "wrote $out/testcard.png and $out/testcard.seg"
