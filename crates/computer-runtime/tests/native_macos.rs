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
        let _active = provider.active_window().unwrap();

        if let Some(window) = windows.as_array().and_then(|items| items.first()) {
            let identity = WindowIdentity {
                window_id: window["window_id"].as_str().unwrap().to_string(),
                pid: i32::try_from(window["pid"].as_i64().unwrap()).unwrap(),
            };
            let shot = provider.screenshot(&identity, 640, 480).unwrap();
            assert_eq!(shot["mime_type"], "image/png");
            assert!(shot["data_base64"].as_str().unwrap().len() > 32);
            assert_eq!(shot["window_id"], identity.window_id);
            assert_eq!(shot["pid"], identity.pid);
        }
    }
}
