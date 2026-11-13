# OER 完整四态导入模板

这是空模板，不含科学数据。空模板预检不能提交。

1. 保留 CSV 表头，使用真实计算结果填入 01_relaxed_four_states.csv。每行一个位点记录，row/site_id/variant 用于对应结构索引；family_key 填材料族标识。E_*_eV 是同一计算模型四态总能量（eV），不是自由能。DeltaE_*_eV 可选；不用时留空，不填零。
2. 每条记录必须有 bare、O、OH、OOH 四态；在 structure_index.csv 中各占一行。archive_member 必须精确指向 relaxed_four_states.tar.gz 内的 EXTXYZ 文件相对路径。sha256 填对应 EXTXYZ 文件字节的 SHA256；source/manifest 保留真实来源及清单信息。不要使用绝对路径或 ../。
3. 用真实 EXTXYZ 填满结构包，保留元素、晶胞、周期性和原子坐标。模板自带的 tar.gz 是空包，必须替换。不要只上传裸结构，也不要用示意图、随机坐标或模拟数值冒充科学数据。
4. method.json 是当前 UMA + 固定修正口径范本。导入其他计算时，应替换为真实模型名称、checkpoint SHA256、同模型气相 H2/H2O 参考能以及有依据的修正项，切勿沿用不匹配的参考能。首版支持 298.15 K、U=0 V RHE。
5. 选择填好的整个文件夹，或同时选择上述能量 CSV、索引 CSV、结构 tar.gz、method.json，先预检，核对错误、重复及方法信息，再提交。不要直接上传外层 ZIP；先解压 ZIP。

科学边界：固定热力学修正估算；非逐结构振动计算；不含显式溶剂。四步自由能总和为 4.92 eV，eta=max(四步自由能)-1.23 V；能量筛选和模型估算不等于稳定性或实验催化活性结论。
