# Ninfa Cortes

Estúdio portátil para Windows que transforma vídeos do YouTube em cortes verticais com legendas editáveis em português.

## Como usar

1. Baixe o ZIP para Windows x64 em [Releases](https://github.com/rodrigobatini/ninfa-cortes/releases) e extraia a pasta inteira.
2. Abra **Ninfa Cortes.exe**. O editor abre em uma janela do Windows e usa o Microsoft Edge WebView2 Runtime do sistema.
3. Cole a URL de um vídeo que você pode usar e escolha até 30 cortes candidatos. A geração baixa o vídeo, transcreve o áudio e seleciona os trechos para revisão.
4. Revise o hook e as legendas, ajuste a posição na prévia e enquadre a facecam quando ela for detectada separadamente. Clique em **Exportar MP4**, depois em **Baixar MP4** para escolher onde salvar o arquivo 1080 × 1920.

A transcrição e a renderização rodam no computador. É preciso Internet para buscar vídeos no YouTube. A transcrição usa CUDA quando disponível e CPU nos demais casos. A biblioteca e os rascunhos ficam em `%LOCALAPPDATA%\BatiniClipes`. Para manter os dados junto do executável, crie uma pasta `dados` ao lado dele antes de abrir. O botão **Encerrar** e o **X** da janela salvam a edição pendente e fecham o motor quando o processamento termina.

## Código

- `app/` contém o editor, a API FastAPI e a renderização.
- `windows/` contém a janela WinForms, o lançador e o ícone.
- `engine/` contém os scripts e os recursos usados do [AI Stream Clipper](https://github.com/arcticloud/ai-stream-clipper), com os avisos originais.

Para recompilar a janela e substituir o código do editor em uma cópia extraída da versão portátil, execute `powershell -ExecutionPolicy Bypass -File .\Build.ps1 -PortableDir "C:\caminho\Ninfa Cortes"`. O script usa o compilador do .NET Framework presente no Windows e baixa o SDK WebView2 do NuGet. Feche o aplicativo antes de executar.

Os componentes de terceiros têm licenças próprias. Consulte [Avisos de terceiros](THIRD-PARTY.md).
