from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pytubefix import YouTube

import os
import uuid
import subprocess
import threading


# =========================================================
# CONFIGURAÇÕES
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
TEMP_DIR = os.path.join(BASE_DIR, "temp")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)


# FFmpeg completo que você instalou
FFMPEG_PATH = "ffmpeg"

# Guarda o progresso dos downloads
downloads = {}

# Evita problemas de acesso simultâneo
downloads_lock = threading.Lock()


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="VideoDown API",
    description="API para análise e processamento de vídeos",
    version="1.0.0"
)


app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR),
    name="static"
)

# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,

    allow_origins=["*"],

    allow_credentials=False,

    allow_methods=["*"],

    allow_headers=["*"]
)


# =========================================================
# MODELOS
# =========================================================

class VideoRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    formato: str


# =========================================================
# CRIAR YOUTUBE
# =========================================================

def criar_youtube(url, callback=None):

    try:

        return YouTube(
            url,
            on_progress_callback=callback
        )

    except Exception as erro:

        print("ERRO PYTUBEFIX:", repr(erro))

        raise HTTPException(
            status_code=400,
            detail=str(erro)
        )
# =========================================================
# ATUALIZAR PROGRESSO
# =========================================================

def atualizar_progresso(
    identificador,
    porcentagem,
    etapa,
    mensagem=""
):

    with downloads_lock:

        if identificador in downloads:

            downloads[identificador]["progresso"] = int(
                max(0, min(100, porcentagem))
            )

            downloads[identificador]["etapa"] = etapa

            downloads[identificador]["mensagem"] = mensagem


# =========================================================
# CALLBACK DE DOWNLOAD
# =========================================================

def criar_callback(
    identificador,
    inicio,
    fim,
    etapa
):

    def callback(stream, chunk, bytes_remaining):

        try:

            tamanho_total = stream.filesize

            if not tamanho_total:
                return

            baixado = tamanho_total - bytes_remaining

            porcentagem_download = (
                baixado / tamanho_total
            ) * 100

            porcentagem_final = (
                inicio +
                (porcentagem_download / 100) *
                (fim - inicio)
            )

            atualizar_progresso(
                identificador,
                porcentagem_final,
                etapa,
                f"Baixando {etapa}..."
            )

        except Exception:
            pass

    return callback


# =========================================================
# ROTA PRINCIPAL
# =========================================================

@app.get("/")
def raiz():
    return FileResponse("index.html")


# =========================================================
# ANALISAR VÍDEO
# =========================================================

@app.post("/api/analisar")
def analisar_video(dados: VideoRequest):

    yt = criar_youtube(dados.url)

    formatos = []

    streams_video = yt.streams.filter(
        only_video=True,
        file_extension="mp4"
    )

    resolucoes = set()

    for stream in streams_video:

        if stream.resolution:
            resolucoes.add(stream.resolution)

    ordem = [
        "144p",
        "240p",
        "360p",
        "480p",
        "720p",
        "1080p",
        "1440p",
        "2160p"
    ]

    for resolucao in ordem:

        if resolucao in resolucoes:
            formatos.append(resolucao)

    if yt.streams.get_audio_only():
        formatos.append("mp3")

    return {

        "sucesso": True,

        "titulo": yt.title,

        "thumbnail": yt.thumbnail_url,

        "duracao": yt.length,

        "autor": yt.author,

        "formatos": formatos
    }


# =========================================================
# INICIAR DOWNLOAD
# =========================================================

@app.post("/api/download")
def iniciar_download(dados: DownloadRequest):

    identificador = str(uuid.uuid4())[:8]

    with downloads_lock:

        downloads[identificador] = {

            "progresso": 0,

            "etapa": "iniciando",

            "mensagem": "Preparando download...",

            "status": "processando",

            "arquivo": None,

            "erro": None,

            "nome": None
        }


    # Executa em uma thread separada
    thread = threading.Thread(
        target=processar_download,
        args=(
            identificador,
            dados.url,
            dados.formato
        ),
        daemon=True
    )

    thread.start()


    return {

        "sucesso": True,

        "id": identificador
    }


# =========================================================
# PROCESSAR DOWNLOAD
# =========================================================

def processar_download(
    identificador,
    url,
    formato
):

    try:

        formato = formato.lower()

        # =================================================
        # YOUTUBE
        # =================================================

        atualizar_progresso(
            identificador,
            1,
            "conectando",
            "Conectando ao vídeo..."
        )


        # =================================================
        # MP3
        # =================================================

        if formato == "mp3":

            callback = criar_callback(
                identificador,
                5,
                75,
                "áudio"
            )

            yt = criar_youtube(
                url,
                callback
            )

            audio = yt.streams.get_audio_only()

            if not audio:

                raise Exception(
                    "Áudio não encontrado."
                )


            arquivo_audio = audio.download(
                output_path=TEMP_DIR,
                filename=f"{identificador}_audio"
            )


            atualizar_progresso(
                identificador,
                75,
                "conversão",
                "Convertendo para MP3..."
            )


            nome_mp3 = f"{identificador}.mp3"

            arquivo_mp3 = os.path.join(
                DOWNLOAD_DIR,
                nome_mp3
            )


            subprocess.run(
                [
                    FFMPEG_PATH,

                    "-y",

                    "-i",
                    arquivo_audio,

                    "-vn",

                    "-codec:a",
                    "libmp3lame",

                    "-b:a",
                    "192k",

                    arquivo_mp3
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )


            if os.path.exists(arquivo_audio):
                os.remove(arquivo_audio)


            atualizar_progresso(
                identificador,
                100,
                "concluído",
                "Download concluído!"
            )


            with downloads_lock:

                downloads[identificador]["status"] = "concluido"

                downloads[identificador]["arquivo"] = arquivo_mp3

                downloads[identificador]["nome"] = (
                    f"{yt.title}.mp3"
                )

            return


        # =================================================
        # VÍDEO
        # =================================================

        resolucoes_validas = [
            "360p",
            "480p",
            "720p",
            "1080p"
        ]

        if formato not in resolucoes_validas:

            raise Exception(
                "Formato inválido."
            )


        # =================================================
        # CALLBACK
        # =================================================

        callback_video = criar_callback(
            identificador,
            5,
            45,
            "vídeo"
        )

        yt = criar_youtube(
            url,
            callback_video
        )


        # =================================================
        # PROCURA VÍDEO
        # =================================================

        video = yt.streams.filter(
            only_video=True,
            file_extension="mp4",
            res=formato
        ).first()


        if not video:

            raise Exception(
                f"{formato} não está disponível."
            )


        # =================================================
        # PROCURA ÁUDIO
        # =================================================

        atualizar_progresso(
            identificador,
            5,
            "preparando",
            "Preparando áudio..."
        )


        audio = yt.streams.get_audio_only()


        if not audio:

            raise Exception(
                "Áudio não encontrado."
            )


        # =================================================
        # DOWNLOAD VÍDEO
        # =================================================

        arquivo_video = video.download(
            output_path=TEMP_DIR,
            filename=f"{identificador}_video.mp4"
        )


        # =================================================
        # DOWNLOAD ÁUDIO
        # =================================================

        callback_audio = criar_callback(
            identificador,
            45,
            75,
            "áudio"
        )

        # Cria novamente com callback para acompanhar áudio
        yt_audio = criar_youtube(
            url,
            callback_audio
        )

        audio = yt_audio.streams.get_audio_only()


        arquivo_audio = audio.download(
            output_path=TEMP_DIR,
            filename=f"{identificador}_audio"
        )


        # =================================================
        # FFMPEG
        # =================================================

        atualizar_progresso(
            identificador,
            76,
            "processando",
            "Juntando vídeo e áudio..."
        )


        nome_final = f"{identificador}.mp4"

        arquivo_final = os.path.join(
            DOWNLOAD_DIR,
            nome_final
        )


        subprocess.run(
            [
                FFMPEG_PATH,

                "-y",

                "-i",
                arquivo_video,

                "-i",
                arquivo_audio,

                "-c:v",
                "copy",

                "-c:a",
                "aac",

                "-shortest",

                arquivo_final
            ],
            check=True,

            stdout=subprocess.DEVNULL,

            stderr=subprocess.DEVNULL
        )


        # =================================================
        # LIMPA TEMPORÁRIOS
        # =================================================

        if os.path.exists(arquivo_video):
            os.remove(arquivo_video)

        if os.path.exists(arquivo_audio):
            os.remove(arquivo_audio)


        # =================================================
        # CONCLUÍDO
        # =================================================

        atualizar_progresso(
            identificador,
            100,
            "concluído",
            "Download concluído!"
        )


        with downloads_lock:

            downloads[identificador]["status"] = "concluido"

            downloads[identificador]["arquivo"] = arquivo_final

            downloads[identificador]["nome"] = (
                f"{yt.title}.mp4"
            )


    except Exception as erro:

        print(
            f"ERRO NO DOWNLOAD {identificador}:",
            erro
        )


        with downloads_lock:

            if identificador in downloads:

                downloads[identificador]["status"] = "erro"

                downloads[identificador]["erro"] = str(erro)

                downloads[identificador]["mensagem"] = (
                    "Erro durante o download."
                )


# =========================================================
# CONSULTAR PROGRESSO
# =========================================================

@app.get("/api/progresso/{identificador}")
def consultar_progresso(identificador: str):

    with downloads_lock:

        dados = downloads.get(identificador)


    if not dados:

        raise HTTPException(
            status_code=404,
            detail="Download não encontrado."
        )


    return dados


# =========================================================
# BAIXAR ARQUIVO FINAL
# =========================================================

@app.get("/api/arquivo/{identificador}")
def baixar_arquivo(identificador: str):

    with downloads_lock:

        dados = downloads.get(identificador)


    if not dados:

        raise HTTPException(
            status_code=404,
            detail="Download não encontrado."
        )


    if dados["status"] != "concluido":

        raise HTTPException(
            status_code=400,
            detail="Download ainda não terminou."
        )


    arquivo = dados["arquivo"]

    if not arquivo or not os.path.exists(arquivo):

        raise HTTPException(
            status_code=404,
            detail="Arquivo não encontrado."
        )


    return FileResponse(
        arquivo,

        media_type=(
            "audio/mpeg"
            if dados["nome"].endswith(".mp3")
            else "video/mp4"
        ),

        filename=dados["nome"]
    )