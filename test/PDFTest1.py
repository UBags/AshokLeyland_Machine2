from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle, TA_CENTER
from reportlab.lib.units import inch, mm, cm
from reportlab.pdfgen import canvas
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, Table, SimpleDocTemplate, Spacer, TableStyle


# class NumberedCanvas(canvas.Canvas):
#
#     def __init__(self, *args, **kwargs):
#         canvas.Canvas.__init__(self, *args, **kwargs)
#         self._codes = []
#
#     def showPage(self):
#         self._codes.append({'code': self._code, 'stack': self._codeStack})
#         self._startPage()
#
#     def save(self):
#         """add page info to each page (page x of y)"""
#         # reset page counter
#         self._pageNumber = 0
#         for code in self._codes:
#             # recall saved page
#             self._code = code['code']
#             self._codeStack = code['stack']
#             self.setFont("Helvetica", 7)
#             self.drawRightString(200*mm, 20*mm,f"page {self._pageNumber+1} of {len(self._codes)}")
#             canvas.Canvas.showPage(self)

class NumberedPageCanvas(canvas.Canvas):
    """
    http://code.activestate.com/recipes/546511-page-x-of-y-with-reportlab/
    http://code.activestate.com/recipes/576832/
    http://www.blog.pythonlibrary.org/2013/08/12/reportlab-how-to-add-page-numbers/
    """
    width, height = landscape(A4)

    def __init__(self, *args, **kwargs):
        """Constructor"""
        super().__init__(*args, **kwargs)
        self.pages = []

    def showPage(self):
        """
        On a page break, add information to the list
        """
        self.pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        """
        Add the page number to each page (page x of y)
        """
        page_count = len(self.pages)

        for page in self.pages:
            self.__dict__.update(page)
            self.draw_page_number(page_count)
            super().showPage()

        super().save()

    def draw_page_number(self, page_count):
        """
        Add the page number
        """
        page = f"Page {self._pageNumber} of {page_count}"
        self.setFont("Times-Bold", 11)
        self.setFillColor(colors.gray)
        self.setStrokeColor('#5B80B2')
        self.drawRightString(NumberedPageCanvas.width - 75, 10, page)

########################################################################
class Test(object):
    """"""

    # ----------------------------------------------------------------------
    def __init__(self):
        """Constructor"""
        self.width, self.height = landscape(A4)
        print(f"{self.width = }, {self.height = }")
        self.styles = getSampleStyleSheet()
        self.header = "To be repeated on each page"

    # ----------------------------------------------------------------------
    def coord(self, x, y, unit=1):
        """
        http://stackoverflow.com/questions/4726011/wrap-text-in-a-table-reportlab
        Helper class to help position flowables in Canvas objects
        """
        x, y = x * unit, self.height - y * unit
        print(f"{x = }{y = }")
        return x, y

    # ----------------------------------------------------------------------
    def run(self):
        """
        Run the report
        """
        self.doc = SimpleDocTemplate("C:/Temp/test1.pdf",
                                     pagesize=landscape(A4),
                                     rightMargin=10,
                                     leftMargin=10,
                                     topMargin=38,
                                     bottomMargin=23)
        self.story : list = [Spacer(1, height=2.5 * inch)]
        self.createLineItems()

        # self.doc.build(self.story, onFirstPage=self.createDocument, onLaterPages=self.header_footer)
        self.doc.build(self.story, onFirstPage=self.createDocument, onLaterPages=self.header_footer, canvasmaker=NumberedPageCanvas)
        print("finished!")

    # ----------------------------------------------------------------------

    def header_footer(self, docCanvas, doc):
        docCanvas.saveState()
        docCanvas.setFont("Times-Bold", 11)
        docCanvas.setFillColor(colors.gray)
        docCanvas.setStrokeColor('#5B80B2')
        docCanvas.drawCentredString(self.width / 2, self.height - 20, self.header)
        # docCanvas.setFont('Times-Roman', 11)
        # docCanvas.drawString(self.width - 100, 4, f"Page {doc.page}")

    def createDocument(self, docCanvas, doc):
        """
        Create the document
        """
        self.c = docCanvas

        self.c.setFont("Times-Bold", 11)
        self.c.setFillColor(colors.gray)
        self.c.setStrokeColor('#5B80B2')
        self.c.drawCentredString(self.width / 2, self.height - 20, self.header)


        normal = self.styles["Normal"]

        header_text = "<b>This is a test header</b>"
        p = Paragraph(header_text, normal)
        p.wrapOn(self.c, self.width, self.height)
        # p.drawOn(self.c, *self.coord(30, 12, mm))
        p.drawOn(self.c, 30, self.height - 50)

        ptext = """Lorem ipsum dolor sit amet, consectetur adipisicing elit,
        sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. 
        Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris 
        nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in
        reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla
        pariatur. Excepteur sint occaecat cupidatat non proident, sunt in 
        culpa qui officia deserunt mollit anim id est laborum."""

        p = Paragraph(ptext, style=normal)
        p.wrapOn(self.c, self.width - 50, self.height)
        p.drawOn(self.c, 30, self.height - 100)

        ptext = """
        At vero eos et accusamus et iusto odio dignissimos ducimus qui
        blanditiis praesentium voluptatum deleniti atque corrupti quos dolores
        et quas molestias excepturi sint occaecati cupiditate non provident,
        similique sunt in culpa qui officia deserunt mollitia animi, id est laborum
        et dolorum fuga. Et harum quidem rerum facilis est et expedita distinctio.
        Nam libero tempore, cum soluta nobis est eligendi optio cumque nihil impedit
        quo minus id quod maxime placeat facere possimus, omnis voluptas assumenda est,
        omnis dolor repellendus. Temporibus autem quibusdam et aut officiis debitis aut
        rerum necessitatibus saepe eveniet ut et voluptates repudiandae sint et
        molestiae non recusandae. Itaque earum rerum hic tenetur a sapiente delectus,
        ut aut reiciendis voluptatibus maiores alias consequatur aut perferendis
        doloribus asperiores repellat.
        """
        p = Paragraph(ptext, style=normal)
        p.wrapOn(self.c, self.width - 50, self.height)
        p.drawOn(self.c, 30, self.height - 175)

    # ----------------------------------------------------------------------
    def createLineItems(self):
        """
        Create the line items
        """
        # text_data = ["Line", "DOS", "Procedure<br />/Modifier",
        #              "Description", "Units", "Billed<br />Charges",
        #              "Type1<br />Reductions", "Type2<br />Reductions",
        #              "Type3<br />Reductions", "Allowance",
        #              "Qualify<br />Code"]
        # d = []
        # font_size = 8
        # centered = ParagraphStyle(name="centered", alignment=TA_CENTER)
        # for text in text_data:
        #     ptext = f"<font size='{font_size}'><b>{text}</b></font>"
        #     p = Paragraph(ptext, centered)
        #     d.append(p)
        # data = [d]
        #
        # line_num = 1
        #
        # formatted_line_data = []
        #
        # for x in range(200):
        #     line_data = [str(line_num), "04/12/2013", "73090",
        #                  "Test Reflexes", "1", "131.00", "0.00",
        #                  "0.00", "0.00", "0.00", "1234"]
        #
        #     for item in line_data:
        #         ptext = f"<font size='{font_size - 1}'>{item}</font>"
        #         p = Paragraph(ptext, centered)
        #         formatted_line_data.append(p)
        #     data.append(formatted_line_data)
        #     formatted_line_data = []
        #     line_num += 1
        #
        # table = Table(data, colWidths=[20, 40, 45, 120, 30, 40,
        #                                50, 50, 50, 50, 30])

        column1Heading = "COLUMN ONE HEADING"
        column2Heading = "COLUMN TWO HEADING"
        # Assemble data for each column using simple loop to append it into data list
        data = [[column1Heading, column2Heading]]
        for i in range(1, 100):
            data.append([str(i), str(i)])

        tableThatSplitsOverPages = Table(data, [6.5 * cm, 6 * cm], repeatRows=1)
        tableThatSplitsOverPages.hAlign = 'CENTER'
        tblStyle = TableStyle([('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
                               ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                               ('FONT', (0, 0), (-1, 0), 'Courier-Bold'),
                               ('LINEBELOW', (0, 0), (-1, -1), 1, colors.black),
                               ('FONT', (0, -1), (-1, -1), 'Courier'),
                               ('BOX', (0, 0), (-1, -1), 1, colors.black),
                               ('BOX', (0, 0), (0, -1), 1, colors.black),
                               ('FONTSIZE', (0, 0), (-1, -1), 8)])
        tblStyle.add('BACKGROUND', (0, 0), (1, 0), colors.lightblue)
        tblStyle.add('BACKGROUND', (0, 1), (-1, -1), colors.white)
        tableThatSplitsOverPages.setStyle(tblStyle)
        self.story.append(tableThatSplitsOverPages)
        #
        # self.story.append(table)


# ----------------------------------------------------------------------
if __name__ == "__main__":
    t = Test()
    t.run()