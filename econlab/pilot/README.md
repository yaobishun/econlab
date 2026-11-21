# 可选试点材料

此目录中的 `econlab_qse.m` 是项目适配器，`provenance.json` 记录所用作者材料的文件名、哈希与验证范围。源码发布包不包含作者论文、原始代码、MAT 数据或参考输出。

**普通启动、导入其他论文和运行自动测试均不需要本试点材料。**

若要重做该试点，请在取得材料使用权限后，自行准备以下文件：

```text
pilot/
  author/
    Quantitative Spatial Economics.pdf
    Helpman_E.m
    Grid30.mat
    functions/
      Hlandprice.m
      Hpindex.m
      Hrealw.m
      Hwelfaregains.m
      solveHLwCtyOpen.m
      solveHLwCtyOpen_E.m
  inputs.mat
  reference.mat
```

`inputs.mat` 和 `reference.mat` 分别为作者 `Grid30.mat` 的输入字段与参考输出字段子集；字段清单和版本差异见 `provenance.json`。不能用模拟或猜测的数据替代作者材料后声称完成作者实验复现。

材料齐全后，才可运行 `tools/bootstrap_pilot.py` 中的试点导入、分析、注册与验证步骤。请勿将这些本地材料连同源码提交到公开仓库，除非已经确认再分发权限。
