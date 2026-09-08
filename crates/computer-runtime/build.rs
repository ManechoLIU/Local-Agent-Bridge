use std::{env, path::PathBuf, process::Command};

fn main() {
    println!("cargo:rerun-if-changed=native/macos_helper.swift");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("macos") {
        return;
    }
    let out = PathBuf::from(env::var_os("OUT_DIR").expect("OUT_DIR"))
        .join("computer-runtime-macos-helper");
    let status = Command::new("xcrun")
        .args(["swiftc", "native/macos_helper.swift", "-o"])
        .arg(&out)
        .status()
        .expect("launch xcrun swiftc");
    if !status.success() {
        panic!("failed to compile macOS computer helper");
    }
    println!(
        "cargo:rustc-env=COMPUTER_RUNTIME_MACOS_HELPER={}",
        out.display()
    );
}
