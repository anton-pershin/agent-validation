(define (problem rebuild-19)
  (:domain blocks-world)
  (:objects a b c d e - block)
  (:init
    (clear a) (hand-empty) (on a c) (on b e) (on c b) (on e d) (on-table d))
  (:goal (and (on a d) (on-table b) (on-table c) (on-table d) (on-table e)))
)
