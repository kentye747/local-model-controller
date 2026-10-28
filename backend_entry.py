"""Frozen, self-contained bridge entry point. No user data is packaged."""
import json, sys
if __name__ == '__main__':
    try:
        import local_controller
        if len(sys.argv)==3 and sys.argv[1]=='--watch-lm':
            from performance import watch_lm
            watch_lm(int(sys.argv[2]))
        else:print(json.dumps(local_controller.main(sys.argv[1:]),ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False));sys.exit(1)
