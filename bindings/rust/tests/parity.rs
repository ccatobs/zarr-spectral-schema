//! cal-schema parity (#3): every constant in calibrate's `cal-schema` crate is
//! reproduced here with the identical identifier and value, generated from the
//! spec. This is the de-risk proof for criterion 1 ("every current cal-schema
//! constant is emitted from the spec; the standalone crate builds") — it both
//! compiles (the const must exist) and asserts the on-disk value.
//!
//! Source of truth for the expected values: calibrate/crates/cal-schema/src/lib.rs.

use zarr_spectral_schema as zss;

#[test]
fn versions_match() {
    assert_eq!(zss::L0_SCHEMA_VERSION, "2");
    assert_eq!(zss::L1_SCHEMA_VERSION, "1.3");
}

#[test]
fn group_names_match() {
    assert_eq!(zss::SOURCE_GROUP, "source");
    assert_eq!(zss::CALIBRATION_GROUP, "calibration");
    assert_eq!(zss::SKYDIFF_GROUP, "skydiff");
    assert_eq!(zss::SKYCHOPDIFF_GROUP, "skychopdiff");
}

#[test]
fn l0_array_names_match() {
    assert_eq!(zss::DATA_5D, "data_5d");
    assert_eq!(zss::SOBSMODE, "sobsmode");
    assert_eq!(zss::MJD, "mjd");
    assert_eq!(zss::EXPTIME, "exptime");
    assert_eq!(zss::ELEVATION, "elevation");
    assert_eq!(zss::AZIMUTH, "azimuth");
    assert_eq!(zss::PAMB, "pamb");
    assert_eq!(zss::TAMB, "tamb");
    assert_eq!(zss::SIGNAL_FREQ, "signal_freq");
    assert_eq!(zss::IMAGE_FREQ, "image_freq");
    assert_eq!(zss::FREQ_RES, "freq_res");
    assert_eq!(zss::FREQ_OFF, "freq_off");
    assert_eq!(zss::REF_CHANNEL, "ref_channel");
    assert_eq!(zss::LLOADSN, "lloadsn");
    assert_eq!(zss::THOT, "thot");
    assert_eq!(zss::TCOLD, "tcold");
    assert_eq!(zss::TSKY_L0, "tsky"); // const name ≠ UPPER(on-disk name)
    assert_eq!(zss::FRONTEND_BACKENDS, "frontend_backends");
    assert_eq!(zss::RAW_FITS_HEADERS, "raw_fits_headers");
    assert_eq!(zss::OTF_LON, "otf_lon");
    assert_eq!(zss::OTF_LAT, "otf_lat");
    assert_eq!(zss::PIXEL_OFFSET_LON, "pixel_offset_lon");
    assert_eq!(zss::PIXEL_OFFSET_LAT, "pixel_offset_lat");
    assert_eq!(zss::INSTMODE_SUBSCAN, "instmode_subscan");
    assert_eq!(zss::CHOP_FREQ_COORD, "chop_freq"); // const name ≠ UPPER(on-disk name)
    assert_eq!(zss::NOD_BEAM, "nod_beam");
}

#[test]
fn l1_array_names_match() {
    assert_eq!(zss::SPECTRA, "spectra");
    assert_eq!(zss::T_SYS, "t_sys");
    assert_eq!(zss::T_INT, "t_int");
    assert_eq!(zss::T_REC_SSB, "t_rec_ssb");
    assert_eq!(zss::GAMMA, "gamma");
    assert_eq!(zss::TAU_SIGNAL, "tau_signal");
    assert_eq!(zss::TAU_IMAGE, "tau_image");
    assert_eq!(zss::T_SKY, "t_sky"); // L1 output; distinct from L0 TSKY_L0
    assert_eq!(zss::FLAGS, "flags");
    assert_eq!(zss::SIGNAL_FREQS, "signal_freqs");
    assert_eq!(zss::IMAGE_FREQS, "image_freqs");
    assert_eq!(zss::OTF_AIRMASS, "otf_airmass");
    assert_eq!(zss::SKYDIFF_SPECTRUM, "spectrum");
    assert_eq!(zss::SKYCHOPDIFF_SPECTRUM, "spectrum");
    assert_eq!(zss::L1_COMPLETE_ATTR, "l1_complete");
}

#[test]
fn list_consts_match() {
    assert_eq!(
        zss::SOURCE_COORDS,
        &[
            "mjd", "exptime", "elevation", "azimuth", "pamb", "tamb", "tsky", "signal_freq",
            "image_freq", "freq_res", "freq_off", "ref_channel", "lloadsn"
        ]
    );
    assert_eq!(
        zss::CAL_COORDS,
        &[
            "mjd", "exptime", "elevation", "azimuth", "pamb", "tamb", "tsky", "thot", "tcold",
            "lloadsn", "signal_freq", "image_freq", "freq_res", "freq_off", "ref_channel"
        ]
    );
    assert_eq!(zss::SOURCE_MODES, &["ON", "OFF", "OTF-ON", "OTF-OFF"]);
    assert_eq!(zss::CAL_MODES, &["HOT", "COL", "COLD", "SKY", "ZERO"]);
}
