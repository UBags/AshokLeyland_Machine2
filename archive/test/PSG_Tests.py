import FreeSimpleGUI as sg

'''
import hashlib

def main():
    # Use this GUI to get your password's hash code
    def HashGeneratorGUI():
        layout = [
            [sg.Text('Login', size=(30, 1), font='Any 15')],
            [sg.Text('Password'), sg.Input(default_text='your password', key='-password-')]
        ]

        window = sg.Window('SHA Generator', layout,
                           auto_size_text=False,
                           default_element_size=(10, 1),
                           text_justification='r',
                           return_keyboard_events=True,
                           grab_anywhere=False)

        while True:
            event, values = window.read()
            if event == sg.WIN_CLOSED:
                break

            password = values['-password-']
            try:
                password_utf = password.encode('utf-8')
                sha1hash = hashlib.sha1()
                sha1hash.update(password_utf)
                password_hash = sha1hash.hexdigest()
                window['hash'].update(password_hash)
            except:
                pass
        window.close()

    # ----------------------------- Paste this code into your program / script -----------------------------
    # determine if a password matches the secret password by comparing SHA1 hash codes
    def PasswordMatches(password, a_hash):
        password_utf = password.encode('utf-8')
        sha1hash = hashlib.sha1()
        sha1hash.update(password_utf)
        password_hash = sha1hash.hexdigest()
        return password_hash == a_hash

    login_password_hash = '6adfb183a4a2c94a2f92dab5ade762a47889a5a1'  # helloworld
    password = sg.popup_get_text(
        'Password: (type gui for other window)', password_char='*')
    if password == 'gui':  # Remove when pasting into your program
        HashGeneratorGUI()  # Remove when pasting into your program
        return  # Remove when pasting into your program
    if password and PasswordMatches(password, login_password_hash):
        print('Login SUCCESSFUL')
    else:
        print('Login FAILED!!')


if __name__ == '__main__':
    sg.theme('DarkAmber')
    main()

'''

def progressBar():
    sg.theme('LightBlue2')
    layout = [[sg.Text('Creating your account...')],
            [sg.ProgressBar(1000, orientation='h', size=(20, 20), key='progbar')],
            [sg.Cancel()]]

    window = sg.Window('Working...', layout)
    for i in range(1000):
        event, values = window.read(timeout=1)
        if event == 'Cancel' or event == sg.WIN_CLOSED:
            break
        window['progbar'].update_bar(i + 1)
    window.close()


def createAccount():
    username : str = ''
    password : str = ''
    email : str = ''
    role : str = 'Operator'
    sg.theme('DarkAmber')
    # layout = [[sg.Text("Enter details : ", size =(20, 1), font=40, justification='c')],
    #           [sg.Text("Create Username", size=(20, 1), font=16), sg.InputText(key='-username-', font=16)],
    #           [sg.Text("Create Password", size=(20, 1), font=16), sg.InputText(key='-password-', font=16, password_char='*')],
    #           [sg.Text("E-mail", size =(20, 1),font=16), sg.InputText(key='-email-', font=16)],
    #           [[sg.Text("Role")], [sg.Combo(values=['Administrator', 'Manager', 'Operator'], default_value='Operator', size=(20, 1), k='-combo-')]],
    #           [sg.Button("Submit"), sg.Button("Cancel")]]
    layout = [
                [sg.Text("Create Username", size=(20, 1), font=16), sg.InputText(key='-username-', font=16)],
                [sg.Text("Create Password", size=(20, 1), font=16), sg.InputText(key='-password-', font=16, password_char='*')],
                [sg.Text("E-mail", size =(20, 1),font=16), sg.InputText(key='-email-', font=16)],
                [sg.Text("Role", size =(20, 1),font=16), sg.Combo(values=['Administrator', 'Manager', 'Operator'], default_value='Operator', font = 16, k='-combo-')],
                [sg.Button("Submit"), sg.Button("Cancel")]
            ]

    window = sg.Window("Create New Account", layout)

    while True:
        event,values = window.read()
        if event == 'Cancel' or event == sg.WIN_CLOSED:
            break
        else:
            if event == "Submit":
                print('Clicked Submit')
                username = values['-username-']
                password = values['-password-']
                email = values['-email-']
                role = values['-combo-']
                progressBar()
                break
    print('Exiting while True')
    window.close()
    return username, password, email, role

# createAccount()


def login():
    username : str = ''
    password : str = ''
    sg.theme("LightBlue2")
    layout = [[sg.Text("Log In", size =(15, 1), font=40)],
            [sg.Text("Username", size =(15, 1), font=16),sg.InputText(key='-username-', font=16)],
            [sg.Text("Password", size =(15, 1), font=16),sg.InputText(key='-password-', password_char='*', font=16)],
            [sg.Button('Login'),sg.Button('Cancel')]]

    window = sg.Window("Login Screen", layout)

    while True:
        event,values = window.read()
        if event == "Cancel" or event == sg.WIN_CLOSED:
            break
        else:
            if event == "Login":
                username = values['-username-']
                password = values['-password-']
                break
    window.close()
    return username, password

def getJustPassword(textToShow : str):
    password = sg.popup_get_text(
        f'{textToShow}', password_char='*')
    return password

# username, password, email, role = createAccount()
# print(username, password, email, role)
# username, password = login()
# print(username, password)
print(getJustPassword("Enter Password 1 "))