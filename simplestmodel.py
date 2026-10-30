    #use tree optimization model (description present in prompt in chatgpt chat)
import cython
import sympy
import numpy
class Reservoir():
    def __init__(self):
        super()
        self.cap=None #capacity
        self.dgrconst=None #unified degradation constant, float or sympy function
        self.cost=None #cost, float
        self.mcost=None #maintenance cost, float
        self.ncons=None # #connections, dict



class Appliance(): #current idea: give an object form for each appliances. other idea: process resultant of all appliances and assign it to one appliance object as D1 node.
    def __init__(self):
        super()
        self.r=None #resistance
        self.z=None #impeadance
        self.e=None #energy consumption
        self.weight=None # priority score. sympy α, α from linr combination function.
        self.emax=None
        self.emin=None
        #^ determine if its better to use attrs or make emax and emin dictionary entries of self.e.



class System():
    def __init__(self):
        self.state={} #dict of all objects part of the microgrid.
        self.ebuffer=None
        self.grid=None #live grid power transmission value. + -> input, - -> output to grid, selling back.
        self.srev=None #solar reservoir
    def E_buffer_calc(self):
        for i in self.state:
            #NOTE:  check how to check type of an object to be of a certain class. this would get rid of the need to give every class an attribute telling logic which class they belong to.
            self.ebuffer6=i.emax-i.e