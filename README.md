# HerbiVision

Ferramenta de desktop em Python para quantificar a área foliar consumida por herbivoria a partir de fotografias de folhas.

Apresentado como painel no 41º Salão de Iniciação Científica da UFSM (JAI 2026), sob o título *"HerbiVision: quantificação semi-automática de herbivoria foliar com Python e OpenCV"*.

## O problema

Medir quanto de uma folha foi consumida por insetos costuma ser feito a olho ou com escalas visuais, o que gera estimativas pouco reprodutíveis. O HerbiVision substitui isso por uma medição em cm², calibrada pela própria foto.

## Como funciona

1. **ROI de calibração** — o usuário desenha um polígono sobre a região que originalmente tinha a área conhecida (por exemplo 6 cm²). O programa converte a área em pixels para uma escala em px/cm.
2. **Pincel manual** — o usuário pinta as regiões consumidas. O pincel tem tamanho ajustável, desfazer, apagar e opção de limitar a pintura ao interior do ROI.
3. **Cálculo** — a área pintada em pixels é convertida para cm² pela calibração, resultando no consumido e no restante.

A área do polígono é calculada pela fórmula do shoelace, e a conversão usa `px/cm = sqrt(área_px / área_cm²)`.

## Recursos

- Polígonos de calibração com vértices editáveis, rotação e inserção/remoção de pontos.
- Pincel com histórico de 20 níveis de desfazer e pré-visualização da área em cm² em tempo real.
- Zoom independente em ambas as etapas, preservando a geometria marcada.
- Leitura da data original da foto via EXIF.
- Importação de lista de amostras por CSV, com detecção automática de codificação e separador.
- Exportação em CSV nos formatos Excel pt-BR, Excel internacional e GraphPad Prism.
- Projetos salvos em JSON para retomar a análise depois.
- Atalhos F1 a F4 para as etapas principais.

## Instalação

```bash
pip install -r requirements.txt
python herbivision.py
```

Requer Python 3.10 ou superior.

## Autor

Kauê Vinicius Romano da Luz — CTISM/UFSM
