# musl-toolchains

```bash
apt install flex bison patch texinfo
pip install -r requirements.txt
```

```bash
python configure.py \
  --target x86_64-linux-musl \
  --cc-flags "-static --static -g0 -Os" \
  --cxx-flags "-static --static -g0 -Os" \
  --ld-flags "-s "
```
