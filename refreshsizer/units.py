"""Display name of the performance unit used throughout the sizing.

Every CPU capacity and demand figure is expressed on the SPECrate2017_int_base scale: SPECrate2017_int_base per
core (SPC) x cores x utilization. Those figures are estimates derived from published results, not SPEC results,
so every report defines "SPEC Score" as an estimate on the SPECrate2017_int_base scale, not a SPEC result. Change the label here (and `unit` in report/i18n.yaml).
"""

UNIT = "SPEC Score"
