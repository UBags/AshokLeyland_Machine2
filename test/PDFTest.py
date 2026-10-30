from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Frame, Spacer
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.pagesizes import A3, A4, landscape, portrait
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER, TA_JUSTIFY
from reportlab.pdfgen import canvas

reportoutputfilepath = "C:\\Temp\\test.pdf"
# doc = SimpleDocTemplate(pdfReportPages, pagesize=A4)

doc = SimpleDocTemplate(reportoutputfilepath,
                            pagesize=landscape(A4),
                            rightMargin=10,
                            leftMargin=10,
                            topMargin=38,
                            bottomMargin=23
                    )
# container for the "Flowable" objects
elements = []
styles=getSampleStyleSheet()
styleN = styles["Normal"]

# Make heading for each column and start data list
column1Heading = "COLUMN ONE HEADING"
column2Heading = "COLUMN TWO HEADING"
# Assemble data for each column using simple loop to append it into data list
data = [[column1Heading,column2Heading]]
for i in range(1,100):
    data.append([str(i),str(i)])

tableThatSplitsOverPages = Table(data, [6.5 * cm, 6 * cm], repeatRows=1)
tableThatSplitsOverPages.hAlign = 'CENTER'
tblStyle = TableStyle([ ('TEXTCOLOR',(0,0),(-1,-1),colors.black),
                        ('VALIGN',(0,0),(-1,-1),'TOP'),
                        ('FONT', (0,0), (-1,0), 'Courier-Bold'),
                        ('LINEBELOW',(0,0),(-1,-1),1,colors.black),
                        ('FONT', (0,-1), (-1,-1), 'Courier'),
                        ('BOX',(0,0),(-1,-1),1,colors.black),
                        ('BOX',(0,0),(0,-1),1,colors.black),
                        ('FONTSIZE', (0,0),(-1,-1), 8)])
tblStyle.add('BACKGROUND',(0,0),(1,0),colors.lightblue)
tblStyle.add('BACKGROUND',(0,1),(-1,-1),colors.white)
tableThatSplitsOverPages.setStyle(tblStyle)
elements.append(tableThatSplitsOverPages)

doc.build(elements)