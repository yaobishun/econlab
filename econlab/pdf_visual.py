"""Full-PDF rendering without OCR. Every page is sent, in bounded batches."""
import base64
import fitz

def batches(path,size=6):
    with fitz.open(path) as pdf:
        for start in range(0,len(pdf),size):
            images=[]
            for index in range(start,min(start+size,len(pdf))):
                page=pdf[index];scale=min(2.0,1800/max(page.rect.width,page.rect.height))
                pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
                images.append({'page':index+1,'url':'data:image/jpeg;base64,'+base64.b64encode(pix.tobytes('jpeg',jpg_quality=85)).decode()})
            yield images
