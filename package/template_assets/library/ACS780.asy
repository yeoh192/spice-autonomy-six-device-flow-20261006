Version 4
SymbolType BLOCK
RECTANGLE Normal -96 -48 96 144
WINDOW 0 0 -48 Bottom 2
WINDOW 3 0 144 Top 2
SYMATTR Value ACS780
SYMATTR Prefix X
SYMATTR ModelFile ACS780.lib
SYMATTR SpiceLine Sensitivity=50m Polarity=2
SYMATTR Description 120 kHz typical bandwidth, and ultra-low power loss: 200 μΩ internal conductor resistance. Typical response time of 3.6μs. Analog ratiometric output from supply voltage; nominal supply voltage 5V.
PIN -96 -16 LEFT 8
PINATTR PinName IP+
PINATTR SpiceOrder 1
PIN -96 112 LEFT 8
PINATTR PinName IP-
PINATTR SpiceOrder 2
PIN 96 -16 RIGHT 8
PINATTR PinName Viout
PINATTR SpiceOrder 3
PIN 96 48 RIGHT 8
PINATTR PinName VCC
PINATTR SpiceOrder 4
PIN 96 112 RIGHT 8
PINATTR PinName DUT_GND
PINATTR SpiceOrder 5
