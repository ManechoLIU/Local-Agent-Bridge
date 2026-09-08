#[cfg(target_os = "macos")]
mod macos {
    use computer_runtime::{ComputerProvider, NativeMacProvider};

    #[test]
    fn native_provider_reads_real_desktop_state_without_ai_bridge_dependency() {
        let provider = NativeMacProvider;
        let permissions = provider.permissions().unwrap();
        assert_eq!(permissions["platform"], "macos");
        assert_eq!(permissions["native_helper"], "computer.runtime.macos.v0");

        let apps = provider.list_apps().unwrap();
        assert!(
            !apps.is_empty(),
            "expected at least one running regular app"
        );
        assert!(apps.iter().all(|app| app.pid > 0));

        let windows = provider.list_windows().unwrap();
        assert!(windows.is_array());
        let _active = provider.active_window().unwrap();
    }
}
