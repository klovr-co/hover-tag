// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// No console window on Windows release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tag_desktop_lib::run()
}
