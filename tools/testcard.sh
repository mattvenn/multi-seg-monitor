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

# 768x592 is the grid, not the screen: 64 columns of 12 px by 37 rows of 16.
# video2seg.py wants exactly that, one source pixel per display pixel.
#
# Everything is written as a target segment intensity 0-15 and then encoded as
# 255*(t/15)^(1/2.2), because video2seg.py linearises the source before
# averaging.  Drawing the bars as a plain luma ramp instead put half the card at
# intensity 0 and a quarter at 1: over half the screen came out black, and a
# black segment that should be black looks the same whether it is stale or not,
# so most of the frame could not show tearing at all.  The fade also stops at
# 30% rather than reaching zero, for the same reason.
#
# Coordinates are the original 624x480 card's, scaled by 768/624 (X) and
# 592/480 (Y) so the same shapes -- circle, bottom bar, black rectangle, 8
# vertical bars -- land in the same relative place on the bigger grid.
ffmpeg -y -v error -f lavfi -i color=c=black:s=768x592 -frames:v 1 -vf \
    "format=gray,geq=lum='if(gt(X\,615)*between(Y\,74\,370), 0, 255*pow(if(between(Y\,493\,543), 13, if(lt(hypot(X-369\,Y-222)\,117), 15, (floor(X/96)+1)*1.875*(1-0.7*Y/592)))/15\, 1/2.2))'" \
    "$out/testcard.png"

cd "$here"
python3 video2seg.py "$out/testcard.png" "$out/testcard.seg"

echo "wrote $out/testcard.png and $out/testcard.seg"
