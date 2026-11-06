class A:
    pB = None  # Class attribute (optional)

    def __init__(self, pA, pB):
        self.pointerA = pA  # Instance attribute
        self.pointerB = pB  # Instance attribute

    class innerA:
        def __init__(self, aParent : 'test.A'):  # No need for `pA` here
            self.aParent = aParent  # Store the parent object

        def someFunc(self):
            varA = self.aParent.pointerA  # Access `pointerA` from the parent object
            varB = self.aParent.pointerB
            print(varA, varB)  # For demonstration

# Example usage
outer_obj = A(42, 52)  # Create an instance of A
inner_obj = outer_obj.innerA(outer_obj)  # Create an instance of innerA, passing the parent object
inner_obj.someFunc()  # Output: 42