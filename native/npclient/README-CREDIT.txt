NPClient compatibility behavior is derived from uglyDwarf's LinuxTrack
implementation, distributed by OpenTrack under contrib/npclient.

Original project: https://github.com/uglyDwarf/linuxtrack
OpenTrack source snapshot: https://github.com/opentrack/opentrack/tree/master/contrib/npclient
License: MIT; see LICENSE.md.

Orienta preserves the upstream NP_GetSignature identity payload and adds
synchronized shared-memory reads, bounded pose conversion, diagnostic logging,
and error-safe handle management.

The game response lookup is sourced from FaceTrackNoIR's supported-games
catalog in data/facetracknoir_supported_games.csv. Its attribution and
permission notice is in data/FACETRACKNOIR-COPYING.txt.
