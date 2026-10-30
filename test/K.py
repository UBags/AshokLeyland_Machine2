@print
def afn():
    print('hello!!')
    return 1000


a, b, *_ = [1, 2, 3, 4, 5]
print(_)

# *x, a, b, *y = [1, 2, 3, 4, 5, 6]
# print(x, y)

class Dog:
    def __init__(self, *args, **kwargs):
        args, kwargs = kwargs, args
        self.age = kwargs[0]
        self.name = args['name']

dog = Dog('rocky', 5)