import unittest
import torch
import torch.nn as nn
from torch.testing._internal.jit_utils import JitTestCase

from torch.testing import FileCheck
from torch.testing._internal.common_quantized import override_quantized_engine
from torch.testing._internal.common_quantization import skipIfNoFBGEMM

from torch.jit._recursive import wrap_cpp_module

import io

if __name__ == '__main__':
    raise RuntimeError("This test file is not meant to be run directly, use:\n\n"
                       "\tpython test/test_jit.py TESTNAME\n\n"
                       "instead.")

class TestFreezing(JitTestCase):
    def test_freeze_module(self):
        class M(nn.Module):
            def __init__(self):
                super(M, self).__init__()
                self.a = 1                      # folded
                self.b = 1.2                    # folded
                self.c = "hello"                # folded
                self.c2 = "hi\xA1"              # not folded
                self.d = [1, 1]                 # folded
                self.e = [1.0, 1.1]             # folded
                self.f = ["hello", "world"]     # folded
                self.f2 = [(1, "Over \u0e55\u0e57 57")]
                self.g = ([1, 2], 3.2, "4.4", torch.tensor([5.5], requires_grad=True))     # folded
                self.h = {"layer" : [torch.tensor([7.7], requires_grad=True)]}
                self.h2 = {"layer\xB1" : [torch.tensor([8.8], requires_grad=True)]}
                self.t = torch.tensor([1.2, 2.4], requires_grad=True)  # folded
                self.ts = [torch.tensor([1.0, 2.0], requires_grad=True), torch.tensor([3.0, 4.0], requires_grad=True)]  # folded
                self.tt = [[torch.tensor([3.3, 2.3], requires_grad=True), None]]

            def forward(self, x):
                return str(self.a) + str(self.b) + self.c + self.c2 + str(self.d) + \
                    str(self.e) + str(self.f) + str(self.f2) + str(self.g) +        \
                    str(self.h) + str(self.h2) + str(self.t) + str(self.ts) + str(self.tt)


        m = torch.jit.script(M())
        m.eval()
        input = torch.randn(2, 2)
        output_s = m.forward(input)
        m._c = torch._C._freeze_module(m._c)
        buffer = io.BytesIO()
        torch.jit.save(m._c, buffer)
        buffer.seek(0)
        m2 = torch.jit.load(buffer)
        # Check if frozen module looks as below:
        # module m {
        #   attributes {
        #     tt = ...
        #   }
        #   ...
        # }
        self.assertFalse(m2._c.hasattr('a'))
        self.assertFalse(m2._c.hasattr('b'))
        self.assertFalse(m2._c.hasattr('c'))
        self.assertFalse(m2._c.hasattr('c2'))
        self.assertFalse(m2._c.hasattr('d'))
        self.assertFalse(m2._c.hasattr('e'))
        self.assertFalse(m2._c.hasattr('f'))
        self.assertFalse(m2._c.hasattr('f2'))
        self.assertFalse(m2._c.hasattr('g'))
        self.assertFalse(m2._c.hasattr('h'))
        self.assertFalse(m2._c.hasattr('h2'))
        self.assertFalse(m2._c.hasattr('t'))
        self.assertFalse(m2._c.hasattr('ts'))
        self.assertFalse(m2._c.hasattr('tt'))
        output_f = m2.forward(input)
        self.assertEqual(output_s, output_f)

    def test_freeze_module_with_submodule(self):
        class SubModule(nn.Module):
            def __init__(self):
                super(SubModule, self).__init__()
                self.a = 11
                self.b = 2

            def forward(self, x):
                return self.a + self.b

        class SubModule2(nn.Module):
            def __init__(self):
                super(SubModule2, self).__init__()
                self.a = 12
                self.b = 2

            def forward(self, x):
                self.b = 30
                return self.a + self.b

        class TestModule(nn.Module):
            def __init__(self):
                super(TestModule, self).__init__()
                self.sub1 = SubModule()
                self.sub2 = SubModule2()
                self.a = 3
                self.b = 4

            def forward(self, x):
                self.b = 20
                return self.sub1(x) + self.a + self.b + self.sub2(x)

        m = torch.jit.script(TestModule())
        m.eval()
        input = torch.randn(2, 2)
        output_s = m.forward(input)
        mf = torch.jit.freeze(m)

        # Check if frozen module looks as below:
        # module m {
        #   attributes {
        #     sub2 = ...
        #      b =
        #   }
        #   ...
        #   submodule {
        #     module m {
        #       attributes {
        #         sub2 = ...
        #         b =
        #       }
        #       ...
        #     }
        #   }
        # }
        mf = mf._c
        self.assertFalse(mf.hasattr('sub1'))
        self.assertFalse(mf.hasattr('a'))
        self.assertTrue(mf.hasattr('b'))
        self.assertTrue(mf.hasattr('sub2'))
        self.assertTrue(mf.sub2.hasattr('b'))   # verify b is preserved in sub2
        self.assertFalse(mf.sub2.hasattr('a'))  # verify a is removed in sub2
        output_f = mf.forward(input)
        self.assertEqual(output_s, output_f)

    def test_freeze_module_with_fork(self):
        class SubModule(nn.Module):
            def __init__(self):
                super(SubModule, self).__init__()
                self.a = torch.ones(20, 20)
                self.b = torch.ones(20, 20)

            def forward(self, x):
                return self.a * self.b + x

        class TestModule(nn.Module):
            def __init__(self):
                super(TestModule, self).__init__()
                self.sub = SubModule()

            def forward(self, x):
                fut = torch.jit._fork(self.sub.forward, x)
                y_hat = self.sub(x)
                y = torch.jit._wait(fut)
                return y_hat + y

        m = torch.jit.script(TestModule())
        m.eval()
        input = torch.randn(20, 20)
        output_s = m.forward(input)
        mf = torch._C._freeze_module(m._c)

        # Check if frozen module looks as below:
        # module m {
        #   attributes {
        #   }
        #   ...
        #   submodule {
        #   }
        # }
        self.assertFalse(mf.hasattr('a'))
        self.assertFalse(mf.hasattr('b'))
        output_f = mf.forward(input)
        self.assertEqual(output_s, output_f)

    def test_freeze_module_with_nested_fork(self):
        class SubModule(nn.Module):
            def __init__(self):
                super(SubModule, self).__init__()
                self.a = torch.ones(20, 20)
                self.b = torch.ones(20, 20)

            def forward(self, x):
                return self.a * self.b + x

        class SubModule2(nn.Module):
            def __init__(self):
                super(SubModule2, self).__init__()
                self.sub = SubModule()
                self.c = torch.ones(20, 20)

            def forward(self, x):
                fut = torch.jit._fork(self.sub.forward, x)
                y_hat = self.sub(x)
                y = torch.jit._wait(fut)
                return y_hat + y + self.c

        class TestModule(nn.Module):
            def __init__(self):
                super(TestModule, self).__init__()
                self.sub = SubModule2()
                self.d = 1

            def forward(self, x):
                fut = torch.jit._fork(self.sub.forward, x)
                y_hat = self.sub(x)
                y = torch.jit._wait(fut)
                self.d = 2
                return y_hat * y + self.d

        m = torch.jit.script(TestModule())
        m.eval()
        input = torch.randn(20, 20)
        output_s = m.forward(input)
        mf = torch._C._freeze_module(m._c)
        # Check if frozen module looks as below:
        # module m {
        #   attributes {
        #   }
        #   ...
        #   submodule {
        #   }
        # }
        self.assertFalse(mf.hasattr('a'))
        self.assertFalse(mf.hasattr('b'))
        self.assertFalse(mf.hasattr('c'))
        self.assertTrue(mf.hasattr('d'))
        output_f = mf.forward(input)
        self.assertEqual(output_s, output_f)


    def test_freeze_module_with_fork2(self):
        @torch.jit.script
        def foo(x, y):
            return x * y

        class TestModule(nn.Module):
            def __init__(self):
                super(TestModule, self).__init__()
                self.a = torch.ones(20, 20)
                self.b = torch.ones(20, 20)

            def forward(self, x):
                fut = torch.jit._fork(foo, self.a, self.b)
                y_hat = foo(self.a, self.b)
                y = torch.jit._wait(fut)
                return y_hat + y

        m = torch.jit.script(TestModule())
        m.eval()
        input = torch.randn(2, 2)
        output_s = m.forward(input)
        mf = torch._C._freeze_module(m._c)

        # Check if frozen module looks as below:
        # module m {
        #   attributes {
        #     self.a = ...
        #     self.b = ..
        #   }
        #   ...
        #   submodule {
        #   }
        # }
        # TODO:  Although there are no mutation, the alias analysis
        # conservatively assumes there is a mutation because attributes are
        # passed to fork subgraph. both 'a' and 'b' are preserved.
        self.assertTrue(mf.hasattr('a'))
        self.assertTrue(mf.hasattr('b'))
        output_f = mf.forward(input)
        self.assertEqual(output_s, output_f)

    def test_freeze_module_with_sharedclasstype(self):
        class SubModule(nn.Module):
            def __init__(self):
                super(SubModule, self).__init__()
                self.a = torch.tensor([1.1])
                self.b = torch.tensor([2.2])

            def forward(self, x):
                return self.a + self.b

            @torch.jit.export
            def modify_a(self, x):
                self.a[0] += 10
                return self. b

            @torch.jit.export
            def modify_b(self, x):
                self.b[0] += 20
                return self.a

        class SubModule2(nn.Module):
            def __init__(self):
                super(SubModule2, self).__init__()
                self.sub = SubModule()
                self.b = torch.tensor([3.3])

            def forward(self, x):
                y = self.sub.modify_b(x)
                return y + self.b

        class TestModule(nn.Module):
            def __init__(self):
                super(TestModule, self).__init__()
                self.sub1 = SubModule()  # sub1 and sub2.sub shared same class type.
                self.sub2 = SubModule2()
                self.a = torch.tensor([4.4])

            def forward(self, x):
                z = self.sub1.modify_a(x)
                return self.sub2(x) + z + self.a

        m = torch.jit.script(TestModule())
        m.eval()
        input = torch.randn(2, 2)
        output_s = m.forward(input)
        mf = torch._C._freeze_module(m._c)

        # Checking if  Frozen module looks as  below
        # module mf {
        #   attributes {
        #     sub1 = ...
        #     sub2 = ...
        #   }
        #   ...
        #   submodules {
        #     module sub1 {
        #       attributes {
        #         a = ...
        #         b = ...
        #       }
        #       ...
        #     }
        #     module sub2 {
        #       attributes {
        #         sub = ...
        #       }
        #       ...
        #       submodule {
        #         module sub {
        #           attributes {
        #             a = ...
        #             b = ...
        #           }
        #           ...
        #         }
        #       }
        #     }
        #   }
        # }

        self.assertTrue(mf.hasattr('sub1'))
        self.assertTrue(mf.sub1.hasattr('a'))
        self.assertTrue(mf.sub1.hasattr('b'))
        self.assertFalse(mf.hasattr('a'))
        self.assertTrue(mf.hasattr('sub2'))
        self.assertTrue(mf.sub2.hasattr('sub'))
        self.assertFalse(mf.sub2.hasattr('b'))
        self.assertTrue(mf.sub2.sub.hasattr('a'))
        self.assertTrue(mf.sub2.sub.hasattr('b'))
        output_f = mf.forward(input)
        self.assertEqual(output_s, output_f)

    def test_freeze_module_with_nestedaliasing(self):
        class SubModule(nn.Module):
            def __init__(self):
                super(SubModule, self).__init__()
                self.a = torch.tensor([1.1])
                self.b = torch.tensor([2.2])

            def forward(self, x):
                return self.a + self.b

            @torch.jit.export
            def modify_a(self, x):
                self.a[0] = 10
                return self. b

            @torch.jit.export
            def modify_b(self, x):
                self.b[0] = 20
                return self.a
        Sub = SubModule()

        class SubModule2(nn.Module):
            def __init__(self):
                super(SubModule2, self).__init__()
                self.sub = Sub  # aliasing

            def forward(self, x):
                return self.sub.a

        class TestModule(nn.Module):
            def __init__(self):
                super(TestModule, self).__init__()
                self.sub1 = Sub  # aliasing
                self.sub2 = SubModule2()

            def forward(self, x):
                z = self.sub1.modify_a(x)
                return self.sub2(x) + z

        m = torch.jit.script(TestModule())
        m.eval()
        mf = torch._C._freeze_module(m._c)
        self.assertTrue(mf.hasattr('sub1'))
        self.assertTrue(mf.sub1.hasattr('a'))
        self.assertFalse(mf.sub1.hasattr('b'))
        self.assertTrue(mf.hasattr('sub2'))
        self.assertTrue(mf.sub2.hasattr('sub'))
        self.assertTrue(mf.sub2.sub.hasattr('a'))  # Freezing detects that self.sub2.sub.a and self.sub1.a are alias
        self.assertFalse(mf.sub2.sub.hasattr('b'))
        input = torch.randn(2, 2)
        output_s = m.forward(input)
        output_f = mf.forward(input)
        self.assertEqual(output_s, output_f)

    # FIXME: JIT is not honoring aliasing. 'Sub' module is copied. As a result
    # Eager and Script modules produce different output.
    def test_freeze_module_with_nestedaliasingscalar(self):
        class SubModule(nn.Module):
            def __init__(self):
                super(SubModule, self).__init__()
                self.a = 1.1
                self.b = 2.2

            def forward(self, x):
                return self.a + self.b

            @torch.jit.export
            def modify_a(self, x):
                self.a = 10.0
                return self. b

            @torch.jit.export
            def modify_b(self, x):
                self.b = 20.0
                return self.a
        Sub = SubModule()

        class SubModule2(nn.Module):
            def __init__(self):
                super(SubModule2, self).__init__()
                self.sub = Sub  # aliasing

            def forward(self, x):
                return self.sub.a

        class TestModule(nn.Module):
            def __init__(self):
                super(TestModule, self).__init__()
                self.sub1 = Sub  # aliasing
                self.sub2 = SubModule2()

            def forward(self, x):
                z = self.sub1.modify_a(x)
                return self.sub2(x) + z
        m = TestModule()
        ms = torch.jit.script(m)
        ms.eval()
        mf = torch._C._freeze_module(ms._c)
        self.assertTrue(mf.hasattr('sub1'))
        self.assertTrue(mf.sub1.hasattr('a'))
        self.assertFalse(mf.sub1.hasattr('b'))
        # sub2 is fully folded becasue self.sub1 and self.sub2.sub are not alias (Scripting bug)
        self.assertFalse(mf.hasattr('sub2'))
        input = torch.randn(2, 2)
        output = m.forward(input)
        output_s = ms.forward(input)
        output_f = mf.forward(input)
        # Should be equal
        self.assertNotEqual(output, output_s)
        self.assertEqual(output_s, output_f)


    def test_freeze_module_with_helperfunction(self):
        class SubModule(nn.Module):
            def __init__(self):
                super(SubModule, self).__init__()
                self.a = 11
                self.b = 2

            def forward(self, x):
                return self.a + self.b

        class TestModule(nn.Module):
            def __init__(self):
                super(TestModule, self).__init__()
                self.sub = SubModule()
                self.a = 3
                self.b = 4

            def forward(self, x):
                self.b = 20
                return self._forward(x) + self.a + self.b

            def _forward(self, x):
                return self.sub(x)
        m = torch.jit.script(TestModule())
        m.eval()
        input = torch.randn(2, 2)
        mf = torch._C._freeze_module(m._c)
        self.assertFalse(mf.hasattr('sub'))
        self.assertFalse(mf.hasattr('a'))
        self.assertTrue(mf.hasattr('b'))
        with self.assertRaisesRegex(RuntimeError, "TestModule does not have a field with name '_forward'"):
            mf._forward(x)

    def test_freeze_module_with_inplace_mutable(self):
        class FreezeMe(torch.jit.ScriptModule):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = [11, 22]

            @torch.jit.script_method
            def forward(self, x):
                for i in range(3):
                    self.a.append(i)
                return self.a

        m = FreezeMe()
        m.eval()
        m_f = torch._C._freeze_module(m._c)
        self.assertTrue(m_f.hasattr('a'))
        m.forward(torch.tensor([3]))
        out = m_f.forward(torch.tensor([5]))
        expected = [11, 22, 0, 1, 2, 0, 1, 2]
        self.assertEqual(out, expected)

    # Mutable attributes
    def test_freeze_module_with_mutable_list(self):
        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = [1, 2]

            def forward(self, x):
                return self.a

        m = FreezeMe()
        m.eval()
        m.a.append(3)
        m_s = torch.jit.script(m)
        v = m_s.a
        v.append(4)
        m_s.a = v
        m_s.eval()
        m_f = torch._C._freeze_module(m_s._c)
        # Post-freezing mutating m_s.a  does not affect m_f (m_f has its own copy).
        v = m_s.a
        v.append(5)
        m_s.a = v
        self.assertFalse(m_f.hasattr('a'))
        out = m_f.forward(torch.tensor([5]))
        expected = [1, 2, 3, 4]
        self.assertEqual(out, expected)

    def test_freeze_module_with_mutable_dict(self):
        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = {"layer" : "4"}

            def forward(self, x):
                return self.a

            @torch.jit.export
            def modify_a(self, x):
                self.a["layer"] = self.a["layer"] + "1"
                return self.a

        m = FreezeMe()
        m.eval()
        m.a["layer2"] = "3"
        m_s = torch.jit.script(m)
        t = torch.tensor(5)
        m_s.modify_a(t)
        m_s.eval()
        m_f = torch._C._freeze_module(m_s._c)
        m.a["layer2"] += "2"
        m_s.modify_a(t)
        self.assertFalse(m_f.hasattr('a'))
        out = m_f.forward(t)
        expected = {"layer" : "411", "layer2" : "3"}
        self.assertEqual(out, expected)

    def test_freeze_module_with_mutable_tensor(self):
        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = torch.tensor([1., 2., 3.])

            def forward(self, x):
                return self.a

        m = FreezeMe()
        m_s = torch.jit.script(m)
        m_s.a[1] += 3.0
        m_s.eval()
        m_f = torch._C._freeze_module(m_s._c)
        # Post-freezing tensor attribute mutations affect m_f.
        # FIXME: deep copy all folded attributes so that m_f has full ownership.
        m_s.a[0] += 5.0
        self.assertFalse(m_f.hasattr('a'))
        out = m_f.forward(torch.tensor([5]))
        expected = [6., 5., 3.]
        self.assertEqual(out, expected)

    def test_freeze_module_with_tuple(self):
        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = (torch.tensor([1, 2, 3, 4, 5, 6]), "hi")

            def forward(self, x):
                if (x[0] == 2.0):
                    self.a[0][0] = 10
                return self.a[0].sum()

        m = FreezeMe()
        m_s = torch.jit.script(m)
        m_s.eval()
        inp = torch.tensor([2.0])
        expected = m_s.forward(inp)
        m_s.a[0][0] = 1
        m_f = torch._C._freeze_module(m_s._c)
        self.assertFalse(m_f.hasattr('a'))
        out = m_f.forward(inp)
        self.assertEqual(out, expected)

    def test_freeze_module_with_tensor(self):
        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = torch.tensor([1, 2, 3, 4, 5, 6])

            def forward(self, x):
                x = self.a.view(2, 3)
                x[0][0] += 10
                return self.a.sum()

        m = FreezeMe()
        m_s = torch.jit.script(m)
        m_s.eval()
        inp = torch.tensor([5])
        expected = m_s.forward(inp)
        m_f = torch._C._freeze_module(m_s._c)
        self.assertTrue(m_f.hasattr('a'))
        m_f.a[0] -= 10
        out = m_f.forward(inp)
        self.assertEqual(out, expected)

    def test_freeze_module_with_list(self):
        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = [torch.tensor([1, 2, 3, 4, 5, 6])]

            def forward(self, x):
                self.a[0][1] += 10
                return self.a[0].sum()

        m = FreezeMe()
        m_s = torch.jit.script(m)
        m_s.eval()
        inp = torch.tensor([5])
        expected = m_s.forward(inp)
        m_s.a[0][1] -= 10
        m_f = torch._C._freeze_module(m_s._c)
        self.assertFalse(m_f.hasattr('a'))
        out = m_f.forward(inp)
        self.assertEqual(out, expected)

    def test_freeze_module_with_aliased_tensor_attr(self):
        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = torch.tensor([1, 2, 3, 4, 5, 6])
                self.b = self.a.view(2, 3)

            def forward(self, x):
                self.b[1] += 10
                return self.a.sum()

        m = FreezeMe()
        m_s = torch.jit.script(m)
        m_s.eval()
        m_f = torch._C._freeze_module(m_s._c)
        self.assertTrue(m_f.hasattr('a'))
        inp = torch.tensor([5])
        out = m_f.forward(inp)
        expected = torch.tensor(51)  # 1+2+3+14+15+16
        self.assertEqual(out, expected)

    def test_freeze_module_with_aliased_tensor_attr2(self):
        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = torch.tensor([1, 2, 3, 4, 5, 6])
                self.b = {"layer" : ([self.a.view(2, 3), torch.tensor([10])], 20)}
                self.c = ([self.a.view(2, 3), torch.tensor([10])], 20)
                self.d = (self.a.view(2, 3), 20)

            def forward(self, x):
                self.d[0][0] += 10
                return self.a.sum()

        m = FreezeMe()
        m_s = torch.jit.script(m)
        m_s.eval()
        inp = torch.tensor([5])
        expected = m_s.forward(inp)
        with self.assertRaisesRegex(RuntimeError, "module contains attributes values that overlaps"):
            m_f = torch._C._freeze_module(m_s._c)

    def test_freeze_module_with_aliased_tensor_attr3(self):
        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = torch.tensor([1, 2, 3, 4, 5, 6])
                self.b = [self.a, torch.tensor([10])]

            def forward(self, x):
                self.a[1] += 10
                return self.b[0].sum()

        m = FreezeMe()
        m_s = torch.jit.script(m)
        m_s.eval()
        inp = torch.tensor([5])
        expected = m_s.forward(inp)
        m_f = torch._C._freeze_module(m_s._c)
        self.assertTrue(m_f.hasattr('a'))
        self.assertTrue(m_f.hasattr('b'))
        out = m_f.forward(inp)
        expected += 10  # account for  self.a += 10.
        self.assertEqual(out, expected)

    def test_freeze_module_with_aliased_tensor_attr4(self):
        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = torch.tensor([1, 2, 3, 4, 5, 6])
                self.b = [self.a, torch.tensor([10])]

            def forward(self, x):
                self.b[0][0] += 10
                return self.a.sum()

        m = FreezeMe()
        m_s = torch.jit.script(m)
        m_s.eval()
        inp = torch.tensor([5])
        expected = m_s.forward(inp)
        m_s.a[0] -= 10
        with self.assertRaisesRegex(RuntimeError, "module contains attributes values that overlaps"):
            m_f = torch._C._freeze_module(m_s._c)

    def test_freeze_module_with_overlapping_attrs(self):
        a = torch.tensor([1, 2, 3, 4, 5, 6])

        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.b = [a.view(3, 2), torch.tensor([10])]
                self.c = (20, a.view(2, 3))

            def forward(self, x):
                self.b[0][0] += 10
                return self.c[1].sum()

        m = FreezeMe()
        m_s = torch.jit.script(m)
        m_s.eval()
        inp = torch.tensor([5])
        expected = m_s.forward(inp)
        a[0] -= 10
        with self.assertRaisesRegex(RuntimeError, "module contains attributes values that overlaps"):
            m_f = torch._C._freeze_module(m_s._c)

    def test_freeze_module_with_aliased_attr(self):
        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = [1, 2, 3, 4, 5, 6]
                self.b = self.a
                self.c = (self.a, 10)

            def forward(self, x):
                self.b[1] += 10
                return str(self.a) + str(self.c)

        m = FreezeMe()
        m_s = torch.jit.script(m)
        m_s.eval()
        m_f = torch._C._freeze_module(m_s._c)
        # FIXME: It should be assertTrue. Currently scripting is making a copy for setting self.b (see #33034)
        self.assertFalse(m_f.hasattr('a'))
        self.assertFalse(m_f.hasattr('c'))
        inp = torch.tensor([5])
        out = m_f.forward(inp)
        expected = m_s.forward(inp)
        self.assertEqual(out, expected)

    # Check attribute a is preserved. Alias analysis detects that 'a' has output writers.
    # In this example, 'a' is not mutated. However, we do not track which sub
    # values of a composite ivalue is mutated.
    def test_freeze_module_with_aliased_attr2(self):
        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = [1, 2, 3, 4, 5, 6]
                self.b = ([11], [10])

            def forward(self, x):
                v = self.a
                self.b = (v, [12])
                v2 = self.b[1]
                v2.append(7)
                return str(v) + str(v2)

        m = FreezeMe()
        m_s = torch.jit.script(m)
        m_s.eval()
        m_f = torch._C._freeze_module(m_s._c)
        self.assertTrue(m_f.hasattr('a'))
        inp = torch.tensor([5])
        out = m_f.forward(inp)
        expected = m.forward(inp)
        self.assertEqual(out, expected)

    def test_freeze_module_with_aliased_attr3(self):
        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = [1, 2, 3, 4, 5, 6]
                self.b = ([11], [10])

            def forward(self, x):
                v = self.a
                v2 = (v, [12])
                v3 = v2[0]
                v3.append(7)
                return str(self.a)

        m = FreezeMe()
        m_s = torch.jit.script(m)
        m_s.eval()
        m_f = torch._C._freeze_module(m_s._c)
        self.assertTrue(m_f.hasattr('a'))
        inp = torch.tensor([5])
        out = m_f.forward(inp)
        expected = m.forward(inp)
        self.assertEqual(out, expected)

    def test_freeze_module_return_self(self):
        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.a = torch.tensor([1., 2., 3.])

            def forward(self, x):
                return self

        m = FreezeMe()
        m_s = torch.jit.script(m)
        m_s.eval()
        with self.assertRaisesRegex(RuntimeError, "attempted to freeze a module that return itself"):
            m_f = torch._C._freeze_module(m_s._c)

    def test_freeze_module_return_sub_module(self):

        class FreezeMe(nn.Module):
            def __init__(self):
                super(FreezeMe, self).__init__()
                self.conv1 = nn.Conv2d(1, 32, 3, 1)

            def forward(self, x):
                return self.conv1

        m = FreezeMe()
        m_s = torch.jit.script(m)
        m_s.eval()
        m_f = torch._C._freeze_module(m_s._c)
        self.assertTrue(m_f.hasattr('conv1'))


    def test_freeze_module_in_training_mode(self):
        class Net(nn.Module):
            def __init__(self):
                super(Net, self).__init__()
                self.conv1 = nn.Conv2d(1, 32, 3, 1)
                self.conv2 = nn.Conv2d(32, 64, 3, 1)
                self.dropout1 = nn.Dropout2d(0.25)
                self.dropout2 = nn.Dropout2d(0.5)
                self.fc1 = nn.Linear(9216, 128)
                self.fc2 = nn.Linear(128, 10)

            def forward(self, x):
                x = self.conv1(x)
                x = nn.functional.relu(x)
                x = self.conv2(x)
                x = nn.functional.max_pool2d(x, 2)
                x = self.dropout1(x)
                x = torch.flatten(x, 1)
                x = self.fc1(x)
                x = nn.functional.relu(x)
                x = self.dropout2(x)
                x = self.fc2(x)
                output = nn.functional.log_softmax(x, dim=1)
                return output

        model = torch.jit.script(Net())
        model.train()

        with self.assertRaisesRegex(RuntimeError, 'Freezing module in training mode is not yet supported'):
            mTrain_freezed = torch._C._freeze_module(model._c)

        model.eval()
        mEval_freezed = torch._C._freeze_module(model._c)
        self.assertFalse(mEval_freezed.hasattr('conv1'))
        self.assertFalse(mEval_freezed.hasattr('conv2'))
        self.assertFalse(mEval_freezed.hasattr('dropout1'))
        self.assertFalse(mEval_freezed.hasattr('training'))
        self.assertFalse(mEval_freezed.hasattr('fc1'))
        self.assertFalse(mEval_freezed.hasattr('dropout2'))
        self.assertFalse(mEval_freezed.hasattr('fc2'))
        with self.assertRaisesRegex(RuntimeError, "does not have a field with name 'state_dict'"):
            print(mEval_freezed.state_dict())
        buffer = io.BytesIO()
        torch.jit.save(mEval_freezed, buffer)
        buffer.seek(0)
        m = torch.jit.load(buffer)
        FileCheck().check_not('GetAttr[name=') \
                   .run(m._c._get_method('forward').graph)

    def test_freeze_module_detach_gradient(self):
        mod = nn.Conv2d(8, 3, 4, 2, 1)
        self.assertTrue(mod.weight.requires_grad)
        smod = torch.jit.script(mod)
        smod.eval()
        fmod = torch._C._freeze_module(smod._c)
        self.assertTrue(mod.weight.requires_grad)
        self.assertTrue(smod.weight.requires_grad)
        self.assertFalse(fmod.hasattr('weight'))
        inp = torch.ones(1, 8, 32, 32)
        out1 = fmod.forward(inp)
        # FIXME: frozen module mutated from outside (original module).
        smod.weight[0, 0, 0, 0] += 100.0
        out2 = fmod.forward(inp)
        out3 = smod(inp)
        self.assertNotEqual(out1, out2)
        self.assertEqual(out2, out3)

    def test_freeze_module_with_user_preserved_attr(self):
        class Module(nn.Module):
            def __init__(self):
                super(Module, self).__init__()
                self.a = torch.tensor([1.1])
                self.b = torch.tensor([2.2])

            def forward(self, x):
                return self.a + self.b

        m = torch.jit.script(Module())
        m.eval()
        fm = torch._C._freeze_module(m._c, ["a"])
        # Attribute "a" is preserved
        self.assertTrue(fm.hasattr("a"))
        self.assertFalse(fm.hasattr("b"))

    def test_freeze_module_with_user_preserved_method(self):
        class Module(nn.Module):
            def __init__(self):
                super(Module, self).__init__()
                self.a = torch.tensor([1.1])
                self.b = torch.tensor([2.2])

            def forward(self, x):
                return self.a + self.b

            @torch.jit.export
            def modify_a(self, x):
                self.a[0] += 10
                return self.b

            @torch.jit.export
            def modify_b(self, x):
                self.b[0] += 20
                return self.a

        m = torch.jit.script(Module())
        m.eval()
        fm = torch._C._freeze_module(m._c, ["modify_a"])
        # Both attribute "a" and method "modify_a" are preserved
        self.assertTrue(fm.hasattr("a"))
        self.assertFalse(fm.hasattr("b"))
        input = torch.randn(2, 2)
        expected = m.forward(input)
        out = fm.forward(input)
        self.assertEqual(out, expected)

    def test_freeze_module_with_user_preserved_method2(self):
        class Module(nn.Module):
            def __init__(self):
                super(Module, self).__init__()
                self.a = torch.tensor([1.1])
                self.b = torch.tensor([2.2])

            def forward(self, x):
                self.b += 10
                return self.a + self.b

            @torch.jit.export
            def modify_a(self, x):
                self.a[0] += 10
                return self.b + self.a

        m = torch.jit.script(Module())
        m.eval()
        fm = torch._C._freeze_module(m._c, ["modify_a"])
        FileCheck().check('prim::GetAttr[name="a"]').run(fm.forward.graph)
        FileCheck().check('prim::GetAttr[name="b"]').run(fm.modify_a.graph)

    @skipIfNoFBGEMM
    def test_module_with_shared_type_instances(self):
        class Child(nn.Module):
            def __init__(self):
                super(Child, self).__init__()
                self.conv1 = nn.Conv2d(1, 1, 1).to(dtype=torch.float32)

            def forward(self, x):
                x = self.conv1(x)
                return x

        class Parent(nn.Module):
            def __init__(self):
                super(Parent, self).__init__()
                self.quant = torch.quantization.QuantStub()
                self.conv1 = nn.Conv2d(1, 1, 1).to(dtype=torch.float32)
                self.child = Child()
                self.child2 = Child()
                self.dequant = torch.quantization.DeQuantStub()

            def forward(self, x):
                x = self.quant(x)
                x = self.conv1(x)
                x = self.child(x)
                x = self.child2(x)
                x = self.dequant(x)
                return x

        def _static_quant(model):
            qModel = torch.quantization.QuantWrapper(model)
            qModel.qconfig = torch.quantization.default_qconfig
            torch.quantization.prepare(qModel, inplace=True)
            qModel(torch.rand(4, 1, 4, 4, dtype=torch.float32))
            torch.quantization.convert(qModel, inplace=True)
            return model

        with override_quantized_engine('fbgemm'):
            data = torch.randn(4, 1, 4, 4, dtype=torch.float32)
            m = Parent().to(torch.float32)
            m = _static_quant(m)
            m = torch.jit.script(m)
            m.eval()
            torch._C._jit_pass_inline(m.graph)
            m_frozen = wrap_cpp_module(torch._C._freeze_module(m._c))
            # Earlier bug resulted in _packed_params set to false.
            FileCheck().check_not('_packed_params = False').run(m_frozen._c.dump_to_str(True, True, False))

            m_res = m(data)
            # It used to segfault while running frozen module.
            m_frozen_res = m_frozen(data)
            self.assertEqual(m_res, m_frozen_res)
