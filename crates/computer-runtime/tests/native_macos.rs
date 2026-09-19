#[cfg(target_os = "macos")]
mod macos {
    use computer_runtime::{ComputerProvider, NativeMacProvider, WindowIdentity};

    #[test]
    fn native_provider_reads_real_desktop_state_without_ai_bridge_dependency() {
        let provider = NativeMacProvider;
        let permissions = provider.permissions().unwrap();
        assert_eq!(permissions["platform"], "macos");
        assert_eq!(permissions["native_helper"], "computer.runtime.macos.v1");

        let apps = provider.list_apps().unwrap();
        assert!(
            !apps.is_empty(),
            "expected at least one running regular app"
        );
        assert!(apps.iter().all(|app| app.pid > 0));

        let windows = provider.list_windows().unwrap();
        assert!(windows.is_array());
        let active = provider.active_window().unwrap();

        if !active.is_null() {
            let identity = WindowIdentity {
                window_id: active["window_id"].as_str().unwrap().to_string(),
                pid: i32::try_from(active["pid"].as_i64().unwrap()).unwrap(),
            };
            if permissions["screen_recording"].as_bool() == Some(true) {
                let shot = provider.screenshot(&identity, 640, 480).unwrap();
                assert_eq!(shot["mime_type"], "image/png");
                assert!(shot["data_base64"].as_str().unwrap().len() > 32);
                assert_eq!(shot["window_id"], identity.window_id);
                assert_eq!(shot["pid"], identity.pid);
                assert!(shot["width"].as_u64().unwrap() <= 640);
                assert!(shot["height"].as_u64().unwrap() <= 480);
                assert_eq!(shot["image_sha256"].as_str().unwrap().len(), 64);
                assert!(shot["bounds"]["width"].as_f64().unwrap() > 0.0);
                assert!(shot["bounds"]["height"].as_f64().unwrap() > 0.0);
            } else {
                assert!(provider.screenshot(&identity, 640, 480).is_err());
            }

            if permissions["accessibility"].as_bool() == Some(false) {
                let error = provider.find(&identity, "test", 1).unwrap_err();
                assert!(error.contains("accessibility permission is required"));
            }
        }
    }
}
