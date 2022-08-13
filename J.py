from config import g
from Element import Element, parse_selector, Selector

def find_in(elem: Element, selector: Selector)->Element|None:
    found: Element|None
    for c in elem.iter_children():
        if elem is c:
            found = c if selector(c) else None
        else:
            found = find_in(c, selector)            
        if found:
            return found
    return None

# J is inspired by $ from jQuery and is basically an ElementProxy
class J:
    _elem: Element
    def __new__(cls, query: str|Element):
        if isinstance(query,str):
            selector = parse_selector(query)
            root: Element = g["root"]
            elem = find_in(root, selector)
            if elem is None:
                return None
            else:
                self = super().__new__(cls)
                self._elem = elem
                return self
        else:
            self._elem = query
