# CM detection policy

- OpenCV applies the same logo-absence duration filter at the beginning and in the middle of a recording. A candidate starting at 0 seconds is eligible; there is no opening exemption.
- Keep the existing candidate threshold (strictly greater than 9 seconds after smoothing), silence snapping, nearby-candidate merging, and final 30–260 second duration filter.
- Chapter-file priority and JoinLogoScp / Fallback mode selection remain unchanged.

## Regression acceptance

- A long opening logo-absence interval and a later long interval both survive candidate extraction and final CM-section construction, with the opening section starting at 0 seconds.
- Short logo-absence intervals at the opening and later in the recording remain excluded by the existing duration filter.
- Run `uv run python -m unittest discover -s tests -p test_cm_sections_detector.py` and `uv run task lint` from `server/`.
- Unit checks do not establish real-recording detection accuracy or reanalyze previously stored CM sections.
