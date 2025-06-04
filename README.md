 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a//dev/null b/README.md
index 0000000000000000000000000000000000000000..66ec20c3103545d844afcbaf41971957dd1c062c 100644
--- a//dev/null
+++ b/README.md
@@ -0,0 +1,19 @@
+# Bot5
+
+Este projeto contém um bot de trading e utilidades associadas.
+
+## Rodando os testes
+
+Para executar a suíte de testes, utilize o `pytest` a partir do diretório raiz do projeto:
+
+```bash
+pytest
+```
+
+Antes de executar, instale as dependências principais com:
+
+```bash
+pip install numpy pandas pandas_ta pytest
+```
+
+Isso garantirá que as bibliotecas necessárias estejam disponíveis para que os testes rodem corretamente.
 
EOF
)
