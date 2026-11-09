build/app.out: app/main.c build/libalpha.a build/libbeta.a
	$(CC) app/main.c -o $@ -Lbuild -lalpha -lbeta
