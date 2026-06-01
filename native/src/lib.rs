// evennia_native — engine-level PyO3 extension scaffold.
//
// This crate is the foundation for native Rust extensions to the Evennia engine.
// It is intentionally empty: add functions here only when Python profiling
// confirms a genuine bottleneck that Rust would fix (attribute-lookup-heavy
// loops don't count — only arithmetic/bitwise hot paths do).
//
// Build (from this directory):
//   VIRTUAL_ENV=/path/to/venv maturin develop --release
//
// Import in Python:
//   try:
//       import evennia_native
//   except ImportError:
//       evennia_native = None  # always provide a Python fallback

use pyo3::prelude::*;

#[pymodule]
fn evennia_native(_m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Functions added here become importable as evennia_native.<name>
    Ok(())
}
