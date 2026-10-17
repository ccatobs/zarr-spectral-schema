# Roadmap

kalibrate, yoda and zarr-spectral-schema together calibrate and reduce CHAI, mini-CHAI, GREAT, upGREAT, 4GREAT and NANTEN2 SMART data, compatible with legacy kalibrate, faster, and easy to spread over a cluster. zarr-spectral-schema is the in-pipeline data format. It replaces FITS and CLASS inside the pipeline so that processing runs fast, and it defines every level from raw data to the cube.

## Not placed yet

- **Specify the reduced spectra and the stitchable cube.** Add the reduced spectra (L2) and the cube (L3) to spec/schema.yaml. Today the spec covers L0 and L1 only, and the cube layout lives in yoda alone. Incremental mapping makes the cube a long-lived product that later runs keep adding to, so its layout needs the same contract as L0 and L1: the fixed grid definition, the summed signal and weight per cell, the scan manifest and the provenance.
